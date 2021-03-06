# -*- coding: utf-8 -*-

import argparse
import re
import os
import datetime
import time
import random
import urllib
import requests
import codecs
import sys
import signal

from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from functools import wraps

main_url_base = r'http://gelbooru.com/index.php?page=post&s=list&tags={url_tags}&pid={pid}'
grab_url_base = r'http://gelbooru.com//images/{0}/{1}/{2}'
referer_base = r'http://gelbooru.com/index.php?page=post&s=view&id={0}'
id_regex = re.compile(r'(?<=thumbnail_)([\da-f]*\.jpg|\.png|\.gif)')
sample_retrieve_regex = re.compile(r'(?<=.com//)(images/[\da-f]{2}/[\da-f]{2}\.jpg|\.png|\.gif)')
referer_regex = re.compile(r'\?[\da-f]*')


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
                    msg = "Caught {e}. Retrying in {sec}".format(e=e, sec=current_delay)
                    print(msg)
                    time.sleep(current_delay)
                    tries_left -= 1
                    current_delay *= back_off
            # Try last time without a catch.
            return f(*args, **kwargs)

        return f_retry

    return deco_retry


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


class InterruptManager(object):
    def __enter__(self):
        self.signal_received = False
        self.old_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self.handler)

    def handler(self, sig, frame):
        self.signal_received = (sig, frame)
        print('Received interrupt signal. Scrapeler will stop shortly.')

    def __exit__(self, type, value, traceback):
        signal.signal(signal.SIGINT, self.old_handler)
        if self.signal_received:
            try:
                self.old_handler(*self.signal_received)
            except KeyboardInterrupt:
                exit('Scrapeler was interrupted and has stopped.')


def generate_blacklist(blacklists):
    temp_list = []
    for b in blacklists:
        path = os.path.abspath(b)
        if os.path.exists(path):
            if os.path.isfile(path):
                with open(path, 'r') as file:
                    contents = file.read()
                    temp_list.extend(contents if type(contents) == list else [contents])
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

    parsed_args = parser.parse_args(expanded_args)

    if parsed_args.dir is not None:
        directory = parsed_args.dir
    else:
        directory = datetime.datetime.now().strftime('{0} %Y%m%d_%H%M').format(parsed_args.tags[0])

    if parsed_args.blacklist is not None:
        blacklist = generate_blacklist(parsed_args.blacklist)

    save_path = os.path.abspath(directory)
    if not os.path.exists(save_path):
        os.makedirs(save_path)

    temp_include = []
    for tag in parsed_args.tags:
        temp_include.append(urllib.parse.quote(tag))
    include_tags = ''.join('%s+' % x.replace('&', '%26').replace(':', '%3a') for x in temp_include)

    if parsed_args.exclude is not None:
        temp_exclude = []
        for tag in parsed_args.exclude:
            temp_exclude.append(urllib.parse.quote(tag))
        exclude_tags = ''.join('-%s+' % x.replace('&', '%26').replace(':', '%3a') for x in temp_exclude)
    else:
        exclude_tags = ''

    if parsed_args.filter:
        filtered_tags = {item: 0 for item in parsed_args.filter}

    scrapeler_args = {
        'tags': parsed_args.tags,
        'exclude': parsed_args.exclude if parsed_args.exclude is not None else [],
        'filter': filtered_tags if parsed_args.filter is not None else {},
        'url_tags': (include_tags + exclude_tags)[:-1],
        'scrape_save_directory': save_path,
        'kwcount': parsed_args.kwcount if parsed_args.kwcount >= -1 else 0,
        'page': parsed_args.page,
        'pagelimit': (parsed_args.pagelimit + parsed_args.page if parsed_args.pagelimit > 0 else -1),
        'scanonly': parsed_args.scanonly,
        'base_delay': 4,
        'short': parsed_args.shortcircuit,
        'batch': parsed_args.batch,
        'blacklist': blacklist if parsed_args.blacklist is not None else [],
    }

    return scrapeler_args


@retry()
def get_soup(url):
    with requests.Session() as sess:
        response = sess.get(url, data=None, headers={
            'User-Agent': UserAgent().random,
            'Accept': '''text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8''',
            'Connection': 'keep-alive',
        }
                            )
        if response.status_code == 200:
            return BeautifulSoup(response.text, "html5lib")
        elif response.status_code >= 500:
            response.raise_for_status()


@retry()
def route_through_subpage(directory_page, subpage_id, image_file_path):
    ret = 0
    with requests.Session() as sess:
        response = sess.get(subpage_id, data=None, headers={
            'User-Agent': UserAgent().firefox,
            'Referer': directory_page,
        })

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

                # this logic is fucked but I don't care
                extension = current_img.split('?')[0][-5:].split('.')[1]
                # image file path always starts life as a .jpg, since that's always what the thumbnails are.
                image_file_path = image_file_path[:-3] + extension
            else:
                img_tag = soup.find('source')
                current_img = img_tag.attrs['src']
                image_file_path = image_file_path[:-3] + 'webm'

            if not os.path.exists(image_file_path):
                delay = 4 + random.uniform(3, 4)
                time.sleep(delay)
                ret = save_image(subpage_id, current_img, image_file_path)
            else:
                print('{0} skipped: Already saved.'.format(current_img))
        except Exception as e:
            print('Unhandled exception during route_through_subpage: {}'.format(e))

    return ret


@retry()
def save_image(referencing_page, current_img, save_to):
    with requests.Session() as sess:
        response = sess.get(current_img, data=None, stream=True,
                            headers={
                                'User-Agent': UserAgent().firefox,
                                'Referer': referencing_page,})

        if response.status_code >= 500:
            response.raise_for_status()

        if response.status_code == 404:
            print('Could not save {current}. 404: File Not found.'.format(current=current_img))
            return 0

        try:
            if response.status_code == 200:
                with open(save_to, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=1024):
                        if chunk:  # filter out keep-alive new chunks
                            f.write(chunk)
                    print(current_img)
                    return 1


        except Exception as e:
            print('Unhandled exception during save_image: {}'.format(e))
            return 0


def scrape_booru(scrapeler_args):
    related_tags = {}
    found_imgs = 0
    total_saved_imgs = 0
    url_tags = scrapeler_args['url_tags']

    page = scrapeler_args['page']
    final_page = scrapeler_args['pagelimit']

    keep_scraping = True
    while keep_scraping:
        saved_imgs = 0
        delay = scrapeler_args['base_delay'] + random.uniform(2, 4)
        time.sleep(delay)
        scrape_url = main_url_base.format(url_tags=url_tags, pid=str(42 * (page - 1)))
        print('\n[{0}] Scraping: {1}, (page {2})'.format(datetime.datetime.now(), scrape_url, page))

        scrape_soup = get_soup(scrape_url)
        results = scrape_soup.findAll('img', class_='preview')

        found_imgs += len(results)
        if len(results) < 42:
            keep_scraping = False

        print('{0} results on page\n'.format(len(results)))

        for result in results:
            with InterruptManager():
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

                if not scrapeler_args['scanonly']:
                    img_fn = id_regex.search(result.attrs['src']).group(1)
                    refer_id = referer_regex.search(result.attrs['src']).group(0)[1:]
                    if scrapeler_args['blacklist'] and img_fn.split('.')[0] in scrapeler_args['blacklist']:
                        print('{0} was filtered. Blacklisted: {1}'.format(referer_base.format(refer_id),
                                                                          img_fn.split('.')[0]))
                        continue
                    if save_current:
                        image_file_path = "{directory}\\{fn}".format(directory=scrapeler_args['scrape_save_directory'],
                                                                     fn=img_fn)
                        delay = scrapeler_args['base_delay'] + random.uniform(0, 2)
                        time.sleep(delay)
                        saved_imgs += route_through_subpage(scrape_url, referer_base.format(refer_id), image_file_path)
                    elif not save_current:
                        print('{0} was filtered. Matched: {1}.'.format(referer_base.format(refer_id), filter_reasons))

        if not scrapeler_args['scanonly']:
            total_saved_imgs += saved_imgs
            print('[{0}] {1} images saved for page {2}. ({3} images saved in total.)'.format(datetime.datetime.now(),
                                                                                             saved_imgs, page,
                                                                                             total_saved_imgs))

        if scrapeler_args['short'] and not scrapeler_args['scanonly'] and saved_imgs == 0:
            print('No images saved with on page {} with shortcircuit on. Stopping.'.format(page))
            keep_scraping = False

        page += 1
        if -1 < final_page == page:
            print('Page limit ({0}) was reached. Stopping.'.format(final_page))
            keep_scraping = False

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
        print('[{ts}] Unhandled exception {e} occured during command {c}'.format(ts=datetime.datetime.now(),
                                                                                 e=ex, c=scrapeler_args['tags']))

    if scrapeler_args['batch']:
        batch_file = scrapeler_args['batch']
        for command in batch_file:
            try:
                delay = random.uniform(300, 450)
                print('[{0}] Sleeping for {1} seconds between commands.'.format(datetime.datetime.now(), delay))
                time.sleep(delay)
                perform_gelbooru_scrape(parse_scrapeler_args(command))
            except Exception as ex:
                print('[{ts}] Unhandled exception {e} occured during command {c}'.format(ts=datetime.datetime.now(),
                                                                                         e=ex, c=command))
        print('Finished.')


if __name__ == '__main__':
    main()
