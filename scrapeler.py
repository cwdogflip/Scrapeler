# -*- coding: utf-8 -*-

import argparse
import re
import os
import datetime
import time
import random as _rand
import urllib
import requests
import codecs
import sys

from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from io import BytesIO
from PIL import Image

main_url_base = r'http://gelbooru.com/index.php?page=post&s=list&tags={url_tags}&pid={pid}'
grab_url_base = r'http://gelbooru.com//images/{0}/{1}/{2}'
referer_base = r'http://gelbooru.com/index.php?page=post&s=view&id={0}'
id_regex = re.compile(r'(?<=thumbnail_)([\da-f]*\.jpg|\.png|\.gif)')
sample_retrieve_regex = re.compile(r'(?<=.com//)(images/[\da-f]{2}/[\da-f]{2}\.jpg|\.png|\.gif)')
referer_regex = re.compile(r'\?[\da-f]*')


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
        else:
            print(response.status_code)
            return None


def parse_scrapeler_args(batch_args=None):
    scrapeler_args = {}

    parser = argparse.ArgumentParser(description='Scrape a booru-style image database. '
                                                 'At least one tag is required to scrape. Choose carefully!')
    parser.add_argument("tags", type=str,
                        help="Enter the tags you want to scrape.\nAt least 1 tag argument is required.",
                        nargs='+',)
    parser.add_argument("-e", "--exclude", type=str, help="Enter tags you want to avoid.",
                        nargs='*', default=None)
    parser.add_argument("-d", "--dir", help="The directory you want the images saved to.", nargs = '?')
    parser.add_argument("-p", "--page", type=int, help="Page you want to start the scraping on", default=1, nargs='?')
    parser.add_argument("-c", "--kwcount", type=int, default=25,
                        help="The number of counted keywords reported. Defaults to 25. If this is 0, Scrapeler will not count keywords. Set this to -1 to grab all related keywords.")
    parser.add_argument("-f", "--keywordfile", default=False, action='store_true',
                        help="Whether or not to store keyword counts in a file. If not specified, Scrapeler will report to the console.")
    parser.add_argument("--pagelimit", type=int, default=-1, help='How many pages to scan before stopping. If below 1, Scrapeler will continue until it finds a page with fewer than maximum images.')
    parser.add_argument("--sleep", default=False, action='store_true', help='If on, Scrapeler will sleep randomly trying to disguise itself.')
    parser.add_argument("--randompagelimit", default=False, action='store_true', help='If on, Scrapeler will stop when it finds no more images or randomly between 10 and 30 pages.')
    parser.add_argument("--scanonly", default=False, action='store_true', help='If on, images will not be saved, but you still collect keyword data.')
    parser.add_argument("--shortcircuit", default=False, action='store_true', help='If on, Scrapeler will stop scraping if it finds nothing on a page that you haven\'t already saved. Does nothing if --scanonly is on.')
    parser.add_argument("--batch", default=None, type=argparse.FileType('r'))

    if not batch_args:
        args = parser.parse_args()
    else:
        args = parser.parse_args(batch_args.split())
        assert args.batch is None

    if args.dir is not None:
        directory = args.dir
        save_path= os.path.abspath(directory)
    else:
        directory = datetime.datetime.now().strftime('{0} %Y%m%d_%H%M').format(args.tags[0])
        save_path= os.path.abspath(directory)

    # Only make the directories if a response file is not about to be invoked.
    if not os.path.exists(save_path):
        os.makedirs(save_path)

    temp_include = []
    for tag in args.tags:
        temp_include.append(urllib.parse.quote(tag))

    include_tags= ''.join('%s+' % x.replace('&', '%26').replace(':','%3a') for x in temp_include)

    if args.exclude is not None:
        temp_exclude = []
        for tag in args.exclude:
            temp_exclude.append(urllib.parse.quote(tag))

        exclude_tags = ''.join('-%s+' % x.replace('&', '%26').replace(':','%3a') for x in temp_exclude)
    else:
        exclude_tags = ''

    base_delay = 4

    scrapeler_args['tags'] = args.tags
    scrapeler_args['exclude'] = args.exclude if args.exclude is not None else []
    scrapeler_args['url_tags'] = (include_tags + exclude_tags)[:-1]
    scrapeler_args['scrape_save_directory'] = save_path
    scrapeler_args['page'] = args.page
    scrapeler_args['kwfile'] = args.keywordfile
    scrapeler_args['kwcount'] = args.kwcount if args.kwcount >= -1 else 0
    scrapeler_args['scanonly'] = args.scanonly
    scrapeler_args['base_delay'] = base_delay
    scrapeler_args['sleep'] = args.sleep
    scrapeler_args['short'] = args.shortcircuit
    scrapeler_args['batch'] = args.batch

    if args.randompagelimit:
        scrapeler_args['pagelimit'] = _rand.randint(10,30)
    else:
        scrapeler_args['pagelimit'] = args.pagelimit if args.pagelimit > 0 else -1

    return scrapeler_args


def route_through_subpage(directory_page, referer_id, image_file_path):
    ret = 0
    with requests.Session() as sess:
        response = sess.get(referer_id, data=None, headers={
            'User-Agent': UserAgent().firefox,
            'Referer': directory_page,
        })
    if not response.status_code == 404:
        soup = BeautifulSoup(response.content, "html5lib", from_encoding=response.encoding)
        try:
            img_tag = soup.find('img', id='image')
            current_img = img_tag.attrs['src']

            if 'sample' in current_img:
                original_url = current_img.replace('samples', 'images').replace('sample_', '')
                split_url = original_url.split('.')
                start = response.text.find('<h5>Options</h5>')
                end = response.text[start:].find('>Original')
                search_text = response.text[start:start+end]
                result = search_text[search_text.find(split_url[1]):]
                result = result[:result.find('"')]
                current_img = split_url[0] + '.' + result

            # this logic is fucked by I don't care
            extension = current_img.split('?')[0][-5:].split('.')[1]
            # image file path always starts life as a .jpg
            image_file_path = image_file_path[:-3] + extension

            if not os.path.exists(image_file_path):
                delay = 4 + _rand.uniform(3, 4)
                time.sleep(delay)
                ret = save_image(referer_id, current_img, image_file_path)
            else:
                print('{0} skipped: Already saved.'.format(current_img))
        except AttributeError as ae:
            img_tag = soup.find('source')
            current_img = img_tag.attrs['src']
            image_file_path = image_file_path[:-3] + 'webm'
            if not os.path.exists(image_file_path):
                delay = 4 + _rand.uniform(3, 4)
                time.sleep(delay)
                ret = save_image(referer_id, current_img, image_file_path)
            else:
                print('{0} skipped: Already saved.'.format(current_img))

        except Exception as e:
            print(e)
    return ret


def save_image(referer_id, current_img, save_to):
    with requests.Session() as sess:
        response = sess.get(current_img, data=None, stream=True, headers={
            'User-Agent': UserAgent().firefox,
            'Referer': referer_id,
        })
        try:
            if response.status_code == 200:
                with open(save_to, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=1024):
                        if chunk:  # filter out keep-alive new chunks
                            f.write(chunk)
                    print(current_img)
                    return 1
        except Exception as e:
            print(e)
            return 0


def scrape_booru(scrapeler_args):

    related_tags = {}
    found = 0
    saved_imgs = 0
    page = scrapeler_args['page']
    url_tags = scrapeler_args['url_tags']
    if scrapeler_args['sleep']:
        next_sleep = _rand.randint(7200, 10800)  # 2 to 3 hours til sleeping.
        print('Will sleep in {0} seconds'.format(next_sleep))
    keep_scraping = True
    if scrapeler_args['pagelimit']:
        final_page = scrapeler_args['pagelimit'] + page
    else:
        final_page = -1

    timestamp = datetime.datetime.now()
    while keep_scraping:
        delay = scrapeler_args['base_delay'] + _rand.uniform(2,4)
        time.sleep(delay)
        scrape_url = main_url_base.format(url_tags=url_tags ,pid=str(42* (page-1)))
        scrape_soup = get_soup(scrape_url)
        print('\nScraping: {0}, (page {1})'.format(scrape_url, page))
        results = scrape_soup.findAll('img', class_='preview')
        found += len(results)
        if len(results) < 42:
            keep_scraping = False

        print('{0} results on page ({1} found, {2} saved.)\n'.format(len(results), found, saved_imgs))

        for result in results:
            if scrapeler_args['kwcount'] != 0:
                for tag in result.attrs['title'].split():
                    if tag in related_tags and 'score' not in tag:
                        related_tags[tag] += 1
                    else:
                        related_tags[tag] = 1

            if not scrapeler_args['scanonly']:
                img_fn = id_regex.search(result.attrs['src']).group(1)
                refer_id = referer_regex.search(result.attrs['src']).group(0)[1:]
                image_file_path = "{directory}\\{fn}".format(directory=scrapeler_args['scrape_save_directory'],
                                                             fn=img_fn)
                delay = scrapeler_args['base_delay'] + _rand.uniform(0,2)
                time.sleep(delay)
                saved_imgs += route_through_subpage(scrape_url, referer_base.format(refer_id), image_file_path)

                # todo if you scrape and find this tag: <title>Gelbooru - Intermission Ad</title>
                # wait 15 seconds then try the page again
        page += 1
        if -1 < final_page == page:
            keep_scraping = False

        if scrapeler_args['short'] and not scrapeler_args['scanonly'] and saved_imgs == 0:
            keep_scraping = False

        if keep_scraping and scrapeler_args['sleep']:
            rn = datetime.datetime.now()
            if rn - timestamp > datetime.timedelta(seconds=next_sleep):
                print('Scrapeler entered sleep. ({0})'.format(datetime.datetime.now()))
                long_delay = _rand.randint(1800, 3600) + _rand.uniform(0,1)  # .5 to 1 hours of sleeping
                while datetime.datetime.now() < rn + datetime.timedelta(seconds=long_delay):
                    time.sleep(120)
                timestamp = datetime.datetime.now()
                print('Scrapeler started scraping again. ({0})'.format(timestamp))
                next_sleep = _rand.randint(7200, 10800)  # 2 to 3 hours til sleeping.
                print('Will sleep in {0} seconds'.format(next_sleep))

    return related_tags


def perform(scrapeler_args):

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
    if scrapeler_args['kwfile']:
        with codecs.open(scrapeler_args['scrape_save_directory'] + '\\keywords.txt', 'w', encoding="utf8") as kwf:
            kwf.write('You scraped for:\r\n')
            for tag in scrapeler_args['tags']:
                kwf.write('{tag} \r\n'.format(tag=tag))
            kwf.write('\r\nYou excluded:\r\n')
            for tag in scrapeler_args['exclude']:
                kwf.write('{tag} \r\n'.format(tag=tag))
            if kwcount > 0:
                kwf.write('\r\nWhich found the following keyword list:\r\n')
                for tag in sorted_related_tags[:kwcount]:
                    kwf.write('{tag} : {count}\r\n'.format(tag=tag, count=related_tags[tag]))
    else:
        for tag in sorted_related_tags[:kwcount]:
            print(tag, related_tags[tag])


def main():
    scrapeler_args = parse_scrapeler_args()

    perform(scrapeler_args)
    if scrapeler_args['batch']:
        batch_file = scrapeler_args['batch']
        for command in batch_file:
            try:
                perform(parse_scrapeler_args(command))
                delay = _rand.uniform(900, 1200)
                print('Sleeping for {} seconds'.format(delay))
                time.sleep(delay)
            except AssertionError as ae:
                print('Batch files may not contain calls to other batch files. Command {0} skipped.'.format(command))
            except Exception as e:
                print(e)

if __name__ == '__main__':
    main()

