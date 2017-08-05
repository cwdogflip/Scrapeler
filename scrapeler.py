# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals, absolute_import

import argparse
import codecs
import collections
import datetime
import functools
import hashlib
import logging
import os
import random
import re
import signal
import sys
import threading
import time

# Attempts at compatibility with Python2.7
try:
    import queue

    _queue = queue
except ImportError:
    import Queue

    queue = None
    _queue = Queue

try:
    from threading import main_thread
except ImportError:
    # main_thread isn't a function in python2. Define it here.
    def main_thread():
        try:
            return main_thread.__mt
        except AttributeError:
            for t in threading.enumerate():
                if t.name == "MainThread":
                    logger.debug('MainThread cached.')
                    main_thread.__mt = t
                    return t


    t = main_thread()  # Cache now, not later.
    del t

# Python 2 missing some an we'd like to use.
try:
    ConnectionError
except NameError:
    class ConnectionError(OSError):
        pass

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

image_directory_template = r'https://gelbooru.com/index.php?page=post&s=list&tags={url_tags}&pid={pid}'
image_url_location_template = r'https://gelbooru.com//images/{0}/{1}/{2}'
image_subpage_template = r'https://gelbooru.com/index.php?page=post&s=view&id={0}'
id_regex = re.compile(r'(?P<image_md5>[\da-fA-F]*)\.(?P<ext>jpg|jpeg|png|gif|webm)')
image_subpage_id_regex = re.compile(r'(?P<id>[\d]+)')
referer_id_regex = re.compile(r'\?[\da-f]*')  # \?(?P<refer_id>[\da-f]*)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger_formatter = logging.Formatter('[%(asctime)s]::[%(levelname)s] - %(message)s')

logger_stdout = logging.StreamHandler(sys.stdout)
logger_stdout.setFormatter(logger_formatter)
logger_stdout.setLevel(logging.DEBUG)
logger.addHandler(logger_stdout)

try:
    __USER_AGENT__ = UserAgent().firefox
except Exception as e:
    __USER_AGENT__ = 'Mozilla/5.0 (compatible, MSIE 11, Windows NT 6.3; Trident/7.0;  rv:11.0) like Gecko'


# Decorators
def retry(caught_exceptions=(ConnectionError, requests.ConnectionError, requests.HTTPError),
          max_tries=3, base_delay=16):
    def deco_retry(f):
        @functools.wraps(f)
        def f_retry(*args, **kwargs):
            tries_left = max_tries
            current_delay = base_delay + random.uniform(0, 1)
            while tries_left > 0:
                try:
                    return f(*args, **kwargs)
                except caught_exceptions as e:
                    msg = "[CONNECTION] Caught {e}. Retrying in {sec}" \
                        .format(e=e, sec=current_delay)
                    logger.warning(msg)
                    time.sleep(current_delay)
                    tries_left -= 1
                    current_delay = base_delay * (2 * (max_tries - tries_left)) + random.uniform(0, 1)

            # Try last time without a catch.
            return f(*args, **kwargs)

        return f_retry

    return deco_retry


# Helper classes
class InterruptManager(object):
    def __init__(self, *args, **kwargs):
        self.signal_received = None
        self.old_handler = None
        self._args = args
        self._kwargs = kwargs
        self._director = kwargs.get('director')

    def __enter__(self):
        self.signal_received = False
        self.old_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self.handler)

    def handler(self, sig, frame):
        self.signal_received = (sig, frame)
        if self._director:
            self._director.quit_event.set()
        logger.info('Received interrupt signal. Scrapeler will stop shortly.')

    def __exit__(self, typ, val, traceback):
        signal.signal(signal.SIGINT, self.old_handler)
        if self.signal_received:
            try:
                self.old_handler(*self.signal_received)
            except KeyboardInterrupt:
                if self._director:
                    self._director.join()
                exit('Scrapeler was interrupted and has stopped.')


class ScrapelerDirector(threading.Thread):
    def __init__(self, max_workers=4):
        logger.debug('Initializing DirectorThread')
        threading.Thread.__init__(self, target=self.direct)
        self.name = 'Thread-ScrapelerDirector'
        self.quit_event = threading.Event()

        self._workers = []
        self.__max_workers = max_workers
        self.__current_saved_count = 0
        self.__total_saved_count = 0

        self.__job_queue = _queue.PriorityQueue()
        self.__active_workers_flag = threading.Event()
        self.__count_lock = threading.Lock()
        self.__worker_lock = threading.RLock()

    def __str__(self):
        with self.__count_lock:
            with self.__worker_lock:
                return '{} - {} - {}'.format(self.name, len(self._workers), len(self.__job_queue.qsize()))

    # Remove dead workers from list.
    def __upkeep(self):
        with self.__worker_lock:
            if self._workers:
                for w in self._workers[:]:
                    # If they don't join quickly, don't block.
                    w.join(.1)
                    # If worker is not alive, it must be done working.
                    if not w.is_alive():
                        if w.saved:
                            with self.__count_lock:
                                self.__current_saved_count += 1
                                self.__total_saved_count += 1
                        self._workers.remove(w)
                    else:  # if w.is_alive
                        self.__active_workers_flag.set()
                        logger.debug('Active Worker {} found. Active flag set.'.format(w))

                # Clear worker flag
                if not self._workers:
                    self.__active_workers_flag.clear()
            else:
                self.__active_workers_flag.clear()

    def __assign_work(self):
        with self.__worker_lock:
            # Don't let a director move on without assigning all work it can.
            while not self.__job_queue.empty() and not self.quit_event.is_set():
                # Don't hire more than max workers
                if len(self._workers) < self.__max_workers:
                    new_hire = ScrapelerWorker(*self.__job_queue.get())
                    new_hire.start()
                    self._workers.append(new_hire)
                    self.__active_workers_flag.set()
                else:
                    break

    def __signal_quitting_time(self):
        with self.__worker_lock:
            for worker in self._workers:
                worker.quit_flag.set()
                worker.quitting_time = datetime.datetime.now() + datetime.timedelta(seconds=15)
                logger.debug('{} signalled to stop.'.format(worker))

    # called externally to let the director know counts need to be reset.
    def signal_new_page(self):
        with self.__count_lock:
            self.__current_saved_count = 0

    def get_total_saved_count(self):
        with self.__count_lock:
            return self.__total_saved_count

    def get_current_saved_count(self):
        with self.__count_lock:
            return self.__current_saved_count

    def has_active_workers(self):
        with self.__worker_lock:
            return self.__active_workers_flag.is_set()

    def get_active_count(self):
        with self.__worker_lock:
            return len(self._workers)

    def get_pending_job_count(self):
        with self.__count_lock:
            return self.__job_queue.qsize()

    def queue_work(self, directory_page, subpage_id, image_save_path):
        self.__job_queue.put((directory_page, subpage_id, image_save_path))

    def direct(self):
        logger.debug('DirectorThread - direct started')
        while not self.quit_event.is_set():
            self.__upkeep()
            self.__assign_work()
            time.sleep(.1)

        logger.debug('DirectorThread - quit_event set')
        self.__signal_quitting_time()

        # Always wait for workers before quitting.
        while self.__active_workers_flag.is_set():
            self.__upkeep()
            time.sleep(.1)
        logger.debug('DirectorThread - direct end.')


class ScrapelerWorker(threading.Thread):
    def __init__(self, *args):
        threading.Thread.__init__(self, target=self.work)
        self.__args = args
        self.saved = 0
        self.quit_flag = threading.Event()
        self.start_time = datetime.datetime.now()
        self.quitting_time = None
        self.errors = []
        # self.daemon = True
        logger.debug('{}:{}'.format(self.name, self.start_time))

    def __str__(self):
        return self.name

    def work(self):
        try:
            self.saved = self._route_through_subpage(*self.__args)
        except Exception as e:
            logger.info('Download thread failed.')
            logger.debug(e, *self.__args)

    @retry()
    def _route_through_subpage(self, directory_page, subpage_id, image_save_path):
        ret = 0
        request_headers = {
            'User-Agent': __USER_AGENT__,
            'Referer': directory_page,
        }
        if not subpage_id.startswith(('http', 'https')):
            subpage_id = 'https:' + subpage_id

        logger.debug(subpage_id)
        with requests.Session() as sess:
            response = sess.get(subpage_id, data=None, headers=request_headers)

        if response.status_code >= 500:
            response.raise_for_status()

        if not response.status_code == 404:
            soup = BeautifulSoup(response.content, "html5lib", from_encoding=response.encoding)
            try:
                img_tag = soup.find('img', id='image')
                if img_tag is not None:
                    current_img_src = img_tag.attrs['src']
                    if 'sample' in current_img_src:
                        original_url = current_img_src \
                            .replace('samples', 'images') \
                            .replace('sample_', '') \
                            .replace('assets2', 'assets')
                        split_url = original_url.split('.')
                        start = response.text.find('Options')
                        end = response.text[start:].find('>Original')
                        search_text = response.text[start:start + end]
                        result = search_text[search_text.find(split_url[1]):]
                        result = result[:result.find('"')]
                        current_img_src = split_url[0] + '.' + result

                    extension = re.search(r'(?P<extension>\.(jpg|jpeg|gif|png))',
                                          current_img_src).group('extension')

                    image_save_path += extension
                else:
                    # No need to try to find extensions here.
                    webm_tag = soup.find('source')
                    current_img_src = webm_tag.attrs['src']
                    image_save_path += '.webm'

                delay = random.uniform(4, 6)
                time.sleep(delay)
                ret = self._save_image(subpage_id, current_img_src, image_save_path)

            except Exception as e:
                logger.error('Unhandled exception during route_through_subpage: {}'
                             .format(e))
                raise e
        return ret

    def _save_image(self, referencing_subpage_id, current_img, image_save_path):
        request_headers = {
            'User-Agent': __USER_AGENT__,
            'Referer': referencing_subpage_id
        }
        if not current_img.startswith(('http', 'https')):
            current_img = 'http:' + current_img

        clean_up = False
        logger.debug(current_img)
        with requests.Session() as sess:
            response = sess.get(current_img, data=None, stream=True, headers=request_headers)

            if response.status_code == 200:
                _current_md5 = hashlib.md5()
                logger.debug('{} saving image.'.format(self.name))
                try:
                    with open(image_save_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=1024):
                            if chunk:  # filter out keep-alive new chunks
                                f.write(chunk)
                                _current_md5.update(chunk)

                            now = datetime.datetime.now()
                            if self.quit_flag.is_set() and self.quitting_time \
                                    and now >= self.quitting_time:
                                logging.info('Thread {} has stopped. {} did not finish.'
                                             .format(self.name, image_save_path))
                                clean_up = True
                                return 0

                            if now >= self.start_time + datetime.timedelta(minutes=20):
                                clean_up = True
                                raise ConnectionError('Download thread {} was taking too long to execute.'
                                                      .format(self.name))

                        logger.info('[SAVED] {} was saved successfully.'
                                    .format(current_img))
                        logger.debug(_current_md5.hexdigest())
                        return 1
                except Exception as e:
                    if isinstance(e, ConnectionError):
                        raise e
                    else:
                        logger.error('Unhandled exception during save_image: {}'
                                     .format(e))
                finally:
                    if clean_up and os.path.isfile(image_save_path):
                        os.remove(image_save_path)

            elif response.status_code >= 500:
                response.raise_for_status()
            elif response.status_code == 404:
                logger.error('Could not save {}. 404: File Not found.'.format(current_img))
                return 0
            else:
                logger.error('Unknown Status code: {}'.format(response.status_code))
            return 0


# Shoutout to Dogflip for writing this
def expand_response_files(raw_args):
    expanded_args = []
    for arg in raw_args:
        if arg.startswith('@'):
            response_file = open(arg[1:], 'r')
            arg_to_add = response_file.read()
        else:
            arg_to_add = arg

        expanded_args.extend(arg_to_add.split())

    return expanded_args


def get_dir_or_file_contents(paths):
    temp_list = []
    if not isinstance(paths, list):
        paths = [paths]

    for p in paths:
        path = os.path.abspath(p)
        if os.path.exists(path):
            if os.path.isfile(path):
                with open(path, 'r') as file:
                    contents = file.read()
                    temp_list.extend(
                        contents if isinstance(paths, list) else contents.split()
                    )
            elif os.path.isdir(path):
                files = os.listdir(path)
                temp_list.extend(files)
            else:
                # I really have no idea how this would happen
                raise OSError("Whatever you did, don't do that.")

    # Get all of the filename up to the last . e.g.: example.image.name.jpg will return everything but '.jpg'
    return {''.join(x.split('.')[:1]): 0 for x in temp_list}


def parse_scrapeler_args(batch_args=None):
    # sys.argv[0] is always 'scrapeler.py'
    raw_args = batch_args.split() if batch_args is not None else sys.argv[1:]
    expanded_args = expand_response_files(raw_args)

    parser = argparse.ArgumentParser(description='Scrape a booru-style image database. '
                                                 'At least one tag is required to scrape. Choose carefully!')
    parser.add_argument("tags", type=str,
                        help="Enter the tags you want to scrape.\nAt least 1 tag argument is required.",
                        nargs='+', )
    parser.add_argument("-e", "--exclude", default=None,
                        nargs='*', type=str,
                        help="Tags that are explicitly avoided in urls. Helps to narrow down searches.")
    parser.add_argument("-f", "--filter", type=str, default=None,
                        nargs='*', help="If an image has a tag in this list, Scrapeler will not save it.")
    parser.add_argument("-d", "--dir", help="The directory you want the images saved to.", nargs='?')
    parser.add_argument("-p", "--page", type=int, help="Page you want to start the scraping on", default=1, nargs='?')
    parser.add_argument("-c", "--kwcount", type=int, default=0,
                        help="The number of counted keywords reported. "
                             "If this is 0, Scrapeler will not count keywords. "
                             "Set this to -1 to grab all scanned keywords.")
    parser.add_argument("--pagelimit", type=int, default=-1,
                        help='How many pages to scan before stopping. If below 1, '
                             'Scrapeler will continue until it finds a page with fewer than maximum images.')
    parser.add_argument("--scanonly", default=False, action='store_true',
                        help='If on, images will not be saved, but you can still collect keyword data.')
    parser.add_argument("--shortcircuit", default=False, action='store_true',
                        help='If on, Scrapeler will stop scraping if it saves nothing on a page. '
                             'Does nothing if --scanonly is on.')
    parser.add_argument("--batch", default=None, type=argparse.FileType('r'),
                        help="Pass a file that contains additional Scrapeler queries here.")
    parser.add_argument("--blacklist", default=None, type=str, nargs='+',
                        help='A directory or file, or series of directories and files, '
                             'that contains images you do not want Scrapeler to save. '
                             'Scrapeler checks against filenames to do this.')
    parser.add_argument("--debug", default=False, action='store_true', help=argparse.SUPPRESS)  # Additional logging
    parser.add_argument("--threads", default=4, type=int, help=argparse.SUPPRESS)  # Download threads.

    env_debug_flag = os.environ.get('SCRAPELER_DEBUG', False)
    parsed_args = parser.parse_args(expanded_args)
    if env_debug_flag or parsed_args.debug:
        logger.setLevel(logging.DEBUG)
        logger.info('Logging level: DEBUG')
        if env_debug_flag:
            logger.info('Debug set through environment.')
    else:
        logger.setLevel(logging.INFO)
        logger.info('Logging level: INFO')

    if parsed_args.dir is not None:
        directory = parsed_args.dir
    else:
        directory = datetime.datetime.now().strftime('{0} %Y%m%d').format(parsed_args.tags[:2])

    blacklist = get_dir_or_file_contents(parsed_args.blacklist) if parsed_args.blacklist is not None else []

    save_path = os.path.abspath(directory)
    if not os.path.exists(save_path):
        os.makedirs(save_path)
        already_saved = []
    else:
        already_saved = get_dir_or_file_contents([save_path])

    include_tags = ''.join('%s+' % x for x in (requests.utils.quote(tag.lower(), safe='')
                                               for tag in parsed_args.tags))

    if parsed_args.exclude is not None:
        exclude_tags = ''.join('-%s+' % x for x in (requests.utils.quote(tag.lower(), safe='')
                                                    for tag in parsed_args.exclude))
    else:
        exclude_tags = ''

    filtered_tags = {item.lower(): 0 for item in parsed_args.filter} if parsed_args.filter else {}

    if parsed_args.scanonly and not parsed_args.kwcount:
        kwcount = -1
    else:
        kwcount = parsed_args.kwcount
    logger.debug('{} keywords will be saved'.format(kwcount))

    scrapeler_args = {
        'tags': parsed_args.tags,
        'exclude': parsed_args.exclude if parsed_args.exclude is not None else [],
        'filter': filtered_tags,
        'url_tags': (include_tags + exclude_tags)[:-1],
        'scrape_save_directory': save_path,
        'kwcount': kwcount if kwcount >= -1 else 0,
        'page': parsed_args.page if parsed_args.page > 0 else 1,
        'pagelimit': (parsed_args.pagelimit + parsed_args.page if parsed_args.pagelimit > 0 else -1),
        'scanonly': parsed_args.scanonly,
        'base_delay': 7,
        'short': parsed_args.shortcircuit,
        'batch': parsed_args.batch,
        'blacklist': blacklist,
        'already_saved': already_saved,
        'debug': env_debug_flag or parsed_args.debug,
        'threads': parsed_args.threads if not (parsed_args.debug or env_debug_flag) else 1,
    }

    return scrapeler_args


@retry()
def get_soup(url):
    with requests.Session() as sess:
        response = sess.get(url, data=None, headers={
            'User-Agent': __USER_AGENT__,
            'Accept': '''text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8''',
            'Connection': 'keep-alive',
        })
        if response.status_code == 200:
            if re.search('[Ss]earch is overloaded[!.]', response.text):
                raise requests.ConnectionError('Search temporarily overloaded. Trying again.')  # Trigger retry.
            return BeautifulSoup(response.text, "html5lib")
        elif response.status_code >= 500:
            response.raise_for_status()


def scrape_booru(scrapeler_args):
    related_tags = collections.defaultdict(int)
    found_imgs = 0
    url_tags = scrapeler_args['url_tags']

    page = scrapeler_args['page']
    final_page = scrapeler_args['pagelimit']

    logger.debug('Starting.')

    director = ScrapelerDirector(scrapeler_args['threads'])
    director.start()
    try:
        keep_scraping = True
        while keep_scraping:
            director.signal_new_page()

            delay = scrapeler_args['base_delay'] + random.uniform(0, 2)
            time.sleep(delay)
            scrape_url = image_directory_template.format(url_tags=url_tags, pid=str(42 * (page - 1)))
            logger.info(
                '[NEW PAGE] Scraping: {}, (page {})'.format(scrape_url, page))

            scrape_soup = get_soup(scrape_url)
            results = scrape_soup.findAll('img', class_='preview')

            found_imgs += len(results)
            if len(results) < 42:
                keep_scraping = False

            logger.info('{} results on page\n'.format(len(results)))

            for count, result in enumerate(results, 1):
                # Delay the keyboard interrupt until the end of the context
                with InterruptManager(director=director):
                    save_current = True
                    filter_reasons = []
                    for tag in result.attrs['title'].split():
                        if 'score' not in tag:
                            related_tags[tag] += 1

                        if tag in scrapeler_args['filter']:
                            scrapeler_args['filter'][tag] += 1
                            save_current = False
                            filter_reasons.append(tag)

                    if scrapeler_args['scanonly']:
                        continue

                    try:
                        image_md5 = id_regex.search(result.attrs['src']).group('image_md5')
                        image_id = image_subpage_id_regex.search(result.attrs['alt']).group('id')
                    except Exception as e:
                        logger.info('{}:{} raised attempting to obtain referer_id. Skipping.'
                                    .format(e.__class__.__name__, e))
                        continue
                    if scrapeler_args['blacklist'] and image_md5 in scrapeler_args['blacklist']:
                        logger.info('[BLACKLISTED] [{}] {} was filtered. Blacklisted.'
                                    .format(count, image_md5))
                        continue
                    if scrapeler_args['already_saved'] and image_md5 in scrapeler_args['already_saved']:
                        logger.info(
                            '[SKIPPED] [{}] {} was skipped. Already saved. ({})'
                                .format(count, image_subpage_template.format(image_id),
                                        image_md5))
                        continue

                    if save_current:
                        image_file_path = "{directory}\\{fn}".format(directory=scrapeler_args['scrape_save_directory'],
                                                                     fn=image_md5)
                        director.queue_work(scrape_url, image_subpage_template.format(image_id),
                                            image_file_path)
                        logger.info('[QUEUED] [{}] {} was queued for download.'
                                    .format(count, image_subpage_template.format(image_id)))
                        time.sleep(.8)

                    elif not save_current:
                        logger.info(
                            '[FILTERED] [{}] {} was filtered. Matched: {}.'
                                .format(count, image_subpage_template.format(image_id),
                                        filter_reasons[:4]))

            if director.has_active_workers():
                logger.info('Waiting for current download(s) to finish.'.format(datetime.datetime.now()))
                next_check = datetime.datetime.now() + datetime.timedelta(seconds=10)
                while director.has_active_workers():
                    if next_check <= datetime.datetime.now():
                        next_check = next_check + datetime.timedelta(seconds=10)
                        logger.info(
                            'Still working... ({} Images left in queue, {} active download(s).)'
                                .format(director.get_pending_job_count(),
                                        director.get_active_count()))
                        logger.debug(threading.enumerate())

            if not scrapeler_args['scanonly']:
                logger.info('[PROGRESS] {} images saved for page {}. ({} images saved in total.)'
                            .format(director.get_current_saved_count(),
                                    page, director.get_total_saved_count()))

            if scrapeler_args['short'] and not scrapeler_args['scanonly'] and director.get_current_saved_count() == 0:
                logger.info('[DONE] No images saved with on page {} with shortcircuit on. Stopping.'
                            .format(page))
                keep_scraping = False

            page += 1
            if -1 < final_page == page:
                logger.info(
                    '[DONE] Page limit ({}) was reached. Stopping.'.format(final_page))
                keep_scraping = False

    except KeyboardInterrupt:
        logger.info('Received interrupt signal. Scrapeler will stop shortly.')
        director.quit_event.set()
        director.join()
        exit('Scrapeler was interrupted and has stopped.')

    # Out of the loop, tell director it's quitting time.
    director.quit_event.set()
    director.join()
    return related_tags


def perform_gelbooru_scrape(scrapeler_args):
    logger.info('\nArguments parsed as:\n'
                'Include tags:{tags}\n'
                'Exclude tags:{exclude}\n'
                'Save images to: {scrape_save_directory}'
                '\nStart on page: {page}'.format(**scrapeler_args))
    if scrapeler_args['scanonly']:
        logger.info('Scan only. No images will be saved!')

    related_tags = scrape_booru(scrapeler_args)
    sorted_related_tags = sorted(related_tags, key=related_tags.get, reverse=True)
    kwcount = len(sorted_related_tags) if scrapeler_args.get('kwcount', 25) == -1 else scrapeler_args['kwcount']

    sorted_filters = sorted(scrapeler_args['filter'], key=scrapeler_args['filter'].get, reverse=True)

    if scrapeler_args['kwcount'] != 0:
        with codecs.open(scrapeler_args['scrape_save_directory'] + '\\keywords.txt', 'w', encoding="utf8") as kwf:
            kwf.write('You scraped for:\r\n')
            for tag in scrapeler_args['tags']:
                kwf.write('{tag} \r\n'.format(tag=tag))
            if scrapeler_args['exclude']:
                kwf.write('\r\nYou excluded:\r\n')
                for tag in scrapeler_args['exclude']:
                    kwf.write('{tag} \r\n'.format(tag=tag))
            if scrapeler_args['filter']:
                kwf.write('\r\nYour filters prevented:\r\n')
                for tag in sorted_filters:
                    if scrapeler_args['filter'][tag] > 0:
                        kwf.write('{tag} : {count}\r\n'.format(tag=tag, count=scrapeler_args['filter'][tag]))
            kwf.write('\r\nWhich found the following keyword list:\r\n')
            for tag in sorted_related_tags[:kwcount]:
                kwf.write('{tag} : {count}\r\n'.format(tag=tag, count=related_tags[tag]))


def main():
    # One of the exception handlers needs this in case someone is really fast on the draw.
    scrapeler_args = {}
    try:
        scrapeler_args = parse_scrapeler_args()
        perform_gelbooru_scrape(scrapeler_args)

        if scrapeler_args['batch']:
            batch_file = scrapeler_args['batch']
            for command in batch_file:
                try:
                    delay = random.uniform(240, 420)
                    logger.info(
                        'Sleeping for {} seconds between commands.'.format(delay))
                    time.sleep(delay)
                    perform_gelbooru_scrape(parse_scrapeler_args(command))
                except Exception as ex:
                    logger.error('Unhandled exception {e} occurred during command {c}'.format(e=ex, c=command))
    except Exception as ex:
        logger.error('Unhandled exception {e} occurred during command {c}'.format(e=ex, c=scrapeler_args.get('tags')))
    except KeyboardInterrupt:
        exit('Scrapeler was interrupted and has stopped.')
    logger.info('Finished.')


if __name__ == '__main__':
    main()
