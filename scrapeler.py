# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals, absolute_import

import argparse
import codecs
import datetime
import logging
import os
import random
import re
import signal
import sys
import threading
import time
from functools import wraps

# Python 2 to 3 compatibility
try:
    import queue as _queue
except ImportError:
    import Queue as _queue

try:
    raise ConnectionRefusedError
except NameError:
    ConnectionRefusedError = OSError
except Exception:
    pass

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

image_directory_template = r'http://gelbooru.com/index.php?page=post&s=list&tags={url_tags}&pid={pid}'
image_url_location_template = r'http://gelbooru.com//images/{0}/{1}/{2}'
image_header_referer_template = r'http://gelbooru.com/index.php?page=post&s=view&id={0}'
id_regex = re.compile(r'(?P<image_md5>[\da-fA-F]*)\.(?P<ext>jpg|jpeg|png|gif|webm)')
referer_id_regex = re.compile(r'\?[\da-f]*')  # \?(?P<refer_id>[\da-f]*)

logger = logging.getLogger('scrapeler')
logger.setLevel(logging.DEBUG)
stdout = logging.StreamHandler(sys.stdout)
stdout.setLevel(logging.DEBUG)
logger.addHandler(stdout)

__USER_AGENT__ = UserAgent().firefox
report_lock = threading.Lock()


# Decorators
def retry(caught_exceptions=(ConnectionRefusedError, requests.ConnectionError, requests.HTTPError),
          max_tries=4, base_delay=256, back_off=2):
    def deco_retry(f):
        @wraps(f)
        def f_retry(*args, **kwargs):
            tries_left = max_tries
            current_delay = base_delay + random.randint(0, base_delay) + random.uniform(0, 1)
            while tries_left > 1:
                try:
                    return f(*args, **kwargs)
                except caught_exceptions as e:
                    msg = "[{ts}] [CONNECTION] Caught {e}. Retrying in {sec}" \
                        .format(ts=datetime.datetime.now(), e=e, sec=current_delay)
                    logger.warning(msg)
                    time.sleep(current_delay)
                    tries_left -= 1
                    current_delay *= back_off
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
        logger.info('Received interrupt signal. Scrapeler will stop shortly.')

    def __exit__(self, typ, val, traceback):
        signal.signal(signal.SIGINT, self.old_handler)
        if self.signal_received:
            try:
                self.old_handler(*self.signal_received)
            except KeyboardInterrupt:
                if self._director:
                    self._director.quit_event.set()
                exit('Scrapeler was interrupted and has stopped.')


# TODO Refactor this so workers are persistent for a group of images.
class ScrapelerDirector(threading.Thread):
    def __init__(self, max_workers=4):
        threading.Thread.__init__(self)
        self.__max_workers = max_workers
        self.__workers = []
        self.__current_saved_count = 0
        self.__total_saved_count = 0
        self.__worker_errors = []

        self.job_queue = _queue.PriorityQueue()
        self.quit_event = threading.Event()

    # Remove dead workers from list.
    def __prune__(self):
        for w in self.__workers[:]:
            if not w.is_alive():
                if w.saved:
                    self.__current_saved_count += 1
                    self.__total_saved_count += 1
                if w.errors:
                    self.__worker_errors.extend(w.errors)
                w.join()
                self.__workers.remove(w)

    # called externally to let the director know counts need to be reset.
    def signal_new_page(self):
        self.__current_saved_count = 0

    def get_total_saved_count(self):
        return self.__total_saved_count

    def get_current_saved_count(self):
        return self.__current_saved_count

    # This probably technically isn't threadsafe
    def has_active_workers(self):
        for w in self.__workers:
            if w.is_alive():
                return True

        self.__prune__()
        return False

    def wait_for_workers(self):
        for w in self.__workers:
            w.join()

    def report_errors(self):
        pass  # TODO

    def __assign_work(self):
        # Don't let a director move on without assigning all work.
        while not self.job_queue.empty():
            # Fire dead workers first.
            self.__prune__()
            # Don't hire more than max workers
            if len(self.__workers) < self.__max_workers:
                new_hire = self.ScrapelerWorker(*self.job_queue.get())
                new_hire.start()
                self.__workers.append(new_hire)
            time.sleep(.1)

    def run(self):
        while not self.quit_event.is_set():
            self.__assign_work()

        # Always wait for workers before quitting.
        self.wait_for_workers()

    class ScrapelerWorker(threading.Thread):
        def __init__(self, *args, **kwargs):
            threading.Thread.__init__(self)
            self.__args = args
            self.saved = 0
            self.errors = []

        def run(self):
            try:
                self.saved = self.__route_through_subpage(*self.__args)
            except Exception as e:
                self.errors.append(e)

        @retry()
        def __route_through_subpage(self, directory_page, subpage_id, image_save_path):
            ret = 0
            request_headers = {
                'User-Agent': __USER_AGENT__,
                'Referer': directory_page,
            }
            with requests.Session() as sess:
                response = sess.get(subpage_id, data=None, headers=request_headers)

            if response.status_code >= 500:
                response.raise_for_status()

            if not response.status_code == 404:
                soup = BeautifulSoup(response.content, "html5lib", from_encoding=response.encoding)
                try:
                    img_tag = soup.find('img', id='image')
                    if img_tag is not None:
                        current_img = img_tag.attrs['src']
                        if 'sample' in current_img:
                            original_url = current_img.replace('samples', 'images').replace('sample_', '')
                            split_url = original_url.split('.')
                            start = response.text.find('<h5>Options</h5>')
                            end = response.text[start:].find('>Original')
                            search_text = response.text[start:start + end]
                            result = search_text[search_text.find(split_url[1]):]
                            result = result[:result.find('"')]
                            current_img = split_url[0] + '.' + result

                        extension = re.search(r'(?P<extension>\.(jpg|jpeg|gif|png))',
                                              current_img).group('extension')

                        image_save_path += extension
                    else:
                        # No need to try to find extensions here.
                        webm_tag = soup.find('source')
                        current_img = webm_tag.attrs['src']
                        image_save_path += '.webm'

                    delay = 3 + random.uniform(3, 4)
                    time.sleep(delay)
                    ret = self.__save_image(subpage_id, current_img, image_save_path)

                except Exception as e:
                    logger.info('[{}] [ERROR] Unhandled exception during route_through_subpage: {}'.format(
                        datetime.datetime.now(), e))
            return ret

        @retry()
        def __save_image(self, referencing_subpage_id, current_img, image_save_path):
            request_headers = {
                'User-Agent': __USER_AGENT__,
                'Referer': referencing_subpage_id
            }
            with requests.Session() as sess:
                response = sess.get(current_img, data=None, stream=True, headers=request_headers)

                if response.status_code == 200:
                    try:
                        with open(image_save_path, 'wb') as f:
                            for chunk in response.iter_content(chunk_size=1024):
                                if chunk:  # filter out keep-alive new chunks
                                    f.write(chunk)
                            logger.info('[{}] [SAVED] {} was saved successfully.'
                                        .format(datetime.datetime.now(), current_img))
                            return 1
                    except Exception as e:
                        logger.exception('[{}] [ERROR] Unhandled exception during save_image: {}'
                                         .format(datetime.datetime.now(), e))

                elif response.status_code >= 500:
                    response.raise_for_status()
                elif response.status_code == 404:
                    logger.info('[{}] [ERROR] Could not save {}. 404: File Not found.'.format(datetime.datetime.now(),
                                                                                              current_img))
                    return 0
                else:
                    logger.info('Unknown Status code: {}'.format(response.status_code))
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
    if not type(paths) is list:
        paths = [paths]

    for p in paths:
        path = os.path.abspath(p)
        if os.path.exists(path):
            if os.path.isfile(path):
                with open(path, 'r') as file:
                    contents = file.read()
                    temp_list.extend(
                        contents if type(contents) is list else contents.split()
                    )
            elif os.path.isdir(path):
                files = os.listdir(path)
                temp_list.extend(files)
            else:
                raise OSError("Whatever you did, don't do that.")

    return {x.split('.')[0]: 0 for x in temp_list}


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
    parser.add_argument("-c", "--kwcount", type=int, default=25,
                        help="The number of counted keywords reported. Defaults to 25. If this is 0, Scrapeler will not count keywords. Set this to -1 to grab all scanned keywords.")
    parser.add_argument("--pagelimit", type=int, default=-1,
                        help='How many pages to scan before stopping. If below 1, Scrapeler will continue until it finds a page with fewer than maximum images.')
    parser.add_argument("--scanonly", default=False, action='store_true',
                        help='If on, images will not be saved, but you still collect keyword data.')
    parser.add_argument("--shortcircuit", default=False, action='store_true',
                        help='If on, Scrapeler will stop scraping if it finds nothing on a page that you haven\'t already saved. Does nothing if --scanonly is on.')
    parser.add_argument("--batch", default=None, type=argparse.FileType('r'),
                        help="Pass a file that contains additional Scrapeler queries here.")
    parser.add_argument("--blacklist", default=None, type=str, nargs='+',
                        help='A directory or file, or series of directories and files, that contains images you do not want Scrapeler to save. Scrapeler checks against filenames to do this.')
    parser.add_argument("--debug", default=False, action='store_true', help='Output additional print statements.')

    parsed_args = parser.parse_args(expanded_args)

    if parsed_args.dir is not None:
        directory = parsed_args.dir
    else:
        directory = datetime.datetime.now().strftime('{0} %Y%m%d_%H%M').format(parsed_args.tags[0])

    blacklist = get_dir_or_file_contents(parsed_args.blacklist) if parsed_args.blacklist is not None else []

    already_saved = []
    save_path = os.path.abspath(directory)
    if not os.path.exists(save_path):
        os.makedirs(save_path)
    else:
        already_saved = get_dir_or_file_contents([save_path])

    temp_include = []
    for tag in parsed_args.tags:
        temp_include.append(requests.utils.quote(tag, safe=''))
    include_tags = ''.join('%s+' % x for x in temp_include)

    if parsed_args.exclude is not None:
        temp_exclude = []
        for tag in parsed_args.exclude:
            temp_exclude.append(requests.utils.quote(tag, safe=''))
        exclude_tags = ''.join('-%s+' % x for x in temp_exclude)
    else:
        exclude_tags = ''

    filtered_tags = {item: 0 for item in parsed_args.filter} if parsed_args.filter else {}

    scrapeler_args = {
        'tags': parsed_args.tags,
        'exclude': parsed_args.exclude if parsed_args.exclude is not None else [],
        'filter': filtered_tags,
        'url_tags': (include_tags + exclude_tags)[:-1],
        'scrape_save_directory': save_path,
        'kwcount': parsed_args.kwcount if parsed_args.kwcount >= -1 else 0,
        'page': parsed_args.page,
        'pagelimit': (parsed_args.pagelimit + parsed_args.page if parsed_args.pagelimit > 0 else -1),
        'scanonly': parsed_args.scanonly,
        'base_delay': 7,
        'short': parsed_args.shortcircuit,
        'batch': parsed_args.batch,
        'blacklist': blacklist,
        'already_saved': already_saved,
        'debug': parsed_args.debug,
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
            return BeautifulSoup(response.text, "html5lib")
        elif response.status_code >= 500:
            response.raise_for_status()


def scrape_booru(scrapeler_args):
    related_tags = {}
    found_imgs = 0
    url_tags = scrapeler_args['url_tags']

    page = scrapeler_args['page']
    final_page = scrapeler_args['pagelimit']

    if scrapeler_args['debug']:
        logger.info('[{}] Starting.'.format(datetime.datetime.now()))

    director = ScrapelerDirector()
    director.start()

    keep_scraping = True
    while keep_scraping:
        director.signal_new_page()

        delay = scrapeler_args['base_delay'] + random.uniform(0, 2)
        time.sleep(delay)
        scrape_url = image_directory_template.format(url_tags=url_tags, pid=str(42 * (page - 1)))
        logger.info('\n[{}] [NEW PAGE] Scraping: {}, (page {})'.format(datetime.datetime.now(), scrape_url, page))

        scrape_soup = get_soup(scrape_url)
        results = scrape_soup.findAll('img', class_='preview')

        found_imgs += len(results)
        if len(results) < 42:
            keep_scraping = False

        logger.info('[{}] [INFO] {} results on page\n'.format(datetime.datetime.now(), len(results)))

        for count, result in enumerate(results, 1):
            with InterruptManager(director=director):
                save_current = True
                filter_reasons = []
                for tag in result.attrs['title'].split():
                    if tag in related_tags:
                        related_tags[tag] += 1
                    elif 'score' not in tag:
                        related_tags[tag] = 1
                    if tag in scrapeler_args['filter']:
                        scrapeler_args['filter'][tag] += 1
                        save_current = False
                        filter_reasons.append(tag)

                if scrapeler_args['scanonly']:
                    continue

                image_md5 = id_regex.search(result.attrs['src']).group('image_md5')
                refer_id = referer_id_regex.search(result.attrs['src']).group(0)[1:]
                if scrapeler_args['blacklist'] and image_md5 in scrapeler_args['blacklist']:
                    logger.info('[{}] [BLACKLISTED] [{}] {} was filtered. Blacklisted.'
                                .format(datetime.datetime.now(), count,
                                        image_md5))
                    continue
                if scrapeler_args['already_saved'] and image_md5 in scrapeler_args['already_saved']:
                    logger.info(
                        '[{}] [SKIPPED] [{}] {} was skipped. Already saved. ({})'
                            .format(datetime.datetime.now(), count, image_header_referer_template.format(refer_id),
                                    image_md5))
                    continue

                if save_current:
                    image_file_path = "{directory}\\{fn}".format(directory=scrapeler_args['scrape_save_directory'],
                                                                 fn=image_md5)
                    director.job_queue.put(
                        [scrape_url, image_header_referer_template.format(refer_id), image_file_path]
                    )
                    logger.info('[{}] [QUEUED] [{}] {} was queued for download.'
                                .format(datetime.datetime.now(), count, image_header_referer_template.format(refer_id)))
                    time.sleep(1)

                elif not save_current:
                    logger.info(
                        '[{}] [FILTERED] [{}] {} was filtered. Matched: {}.'
                            .format(datetime.datetime.now(), count, image_header_referer_template.format(refer_id),
                                    filter_reasons))

        # Wait for workers to finish before going to the next page, or short circuiting will behave weirdly.
        if director.has_active_workers():
            logger.info('[{}] [INFO] Waiting for current downloads to finish.'.format(datetime.datetime.now()))
            updater = 0
            while director.has_active_workers():
                time.sleep(.1)
                updater += 1
                if not (updater % 100):
                    logger.info('[{}] [INFO] Still working...'
                                .format(datetime.datetime.now()))

        if not scrapeler_args['scanonly']:
            logger.info('[{}] [PROGRESS] {} images saved for page {}. ({} images saved in total.)'
                        .format(datetime.datetime.now(), director.get_current_saved_count(),
                                page, director.get_total_saved_count()))

        if scrapeler_args['short'] and not scrapeler_args['scanonly'] and director.get_current_saved_count() == 0:
            logger.info('[{}] [DONE] No images saved with on page {} with shortcircuit on. Stopping.'
                        .format(datetime.datetime.now(), page))
            keep_scraping = False

        page += 1
        if -1 < final_page == page:
            logger.info(
                '[{}] [DONE] Page limit ({}) was reached. Stopping.'.format(datetime.datetime.now(), final_page))
            keep_scraping = False

    director.quit_event.set()
    return related_tags


def perform_gelbooru_scrape(scrapeler_args):
    print('\nArguments parsed as:')
    print('Include tags:', scrapeler_args['tags'])
    print('Exclude tags:', scrapeler_args['exclude'])
    print('Save images to:', scrapeler_args['scrape_save_directory'])
    print('Start on page:', scrapeler_args['page'])
    if scrapeler_args['scanonly']:
        print('Scan only')

    related_tags = scrape_booru(scrapeler_args)
    sorted_related_tags = sorted(related_tags, key=related_tags.get, reverse=True)
    if scrapeler_args['kwcount'] == -1:
        kwcount = len(sorted_related_tags)
    else:
        kwcount = scrapeler_args['kwcount']

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
    scrapeler_args = parse_scrapeler_args()
    try:
        perform_gelbooru_scrape(scrapeler_args)
    except Exception as ex:
        logger.error('[{ts}] Unhandled exception {e} occurred during command {c}'.format(ts=datetime.datetime.now(),
                                                                                         e=ex,
                                                                                         c=scrapeler_args['tags']))

    if scrapeler_args['batch']:
        batch_file = scrapeler_args['batch']
        for command in batch_file:
            try:
                delay = random.uniform(300, 450)
                logger.info('[{0}] Sleeping for {1} seconds between commands.'.format(datetime.datetime.now(), delay))
                time.sleep(delay)
                perform_gelbooru_scrape(parse_scrapeler_args(command))
            except Exception as ex:
                logger.error('[{ts}] Unhandled exception {e} occurred during command {c}'
                             .format(ts=datetime.datetime.now(), e=ex, c=command))
    print('Finished.')


if __name__ == '__main__':
    main()
