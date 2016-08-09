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

from bs4 import BeautifulSoup
from fake_useragent import UserAgent

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
    # sys.argv[0] is always 'scrapeler.py'
    raw_args = batch_args.split() if batch_args is not None else sys.argv[1:]

    expanded_args = expand_response_files(raw_args).split();

    parser = argparse.ArgumentParser(description='Scrape a booru-style image database. '
                                                 'At least one tag is required to scrape. Choose carefully!')
    parser.add_argument("tags", type=str,
                        help="Enter the tags you want to scrape.\nAt least 1 tag argument is required.",
                        nargs='+',)
    parser.add_argument("-e", "--exclude", default=None,
                        nargs='*', type=str, help="Tags that are explicitly avoided in urls. Helps to narrow down searches.")
    parser.add_argument("-f", "--filter", type=str, default=None,
                        nargs='*', help="If an image has a tag in this list, Scrapeler will not save it.")
    parser.add_argument("-d", "--dir", help="The directory you want the images saved to.", nargs = '?')
    parser.add_argument("-p", "--page", type=int, help="Page you want to start the scraping on", default=1, nargs='?')
    parser.add_argument("-c", "--kwcount", type=int, default=25,
                        help="The number of counted keywords reported. Defaults to 25. If this is 0, Scrapeler will not count keywords. Set this to -1 to grab all scanned keywords.")
    parser.add_argument("--pagelimit", type=int, default=-1, help='How many pages to scan before stopping. If below 1, Scrapeler will continue until it finds a page with fewer than maximum images.')
    parser.add_argument("--scanonly", default=False, action='store_true', help='If on, images will not be saved, but you still collect keyword data.')
    parser.add_argument("--shortcircuit", default=False, action='store_true', help='If on, Scrapeler will stop scraping if it finds nothing on a page that you haven\'t already saved. Does nothing if --scanonly is on.')
    parser.add_argument("--batch", default=None, type=argparse.FileType('r'), help="Pass a file that contains additional Scrapeler queries here.")

    parsed_args = parser.parse_args(expanded_args)

    if parsed_args.dir is not None:
        directory = parsed_args.dir
    else:
        directory = datetime.datetime.now().strftime('{0} %Y%m%d_%H%M').format(parsed_args.tags[0])

    save_path = os.path.abspath(directory)
    if not os.path.exists(save_path):
        os.makedirs(save_path)

    temp_include = []
    for tag in parsed_args.tags:
        temp_include.append(urllib.parse.quote(tag))
    include_tags= ''.join('%s+' % x.replace('&', '%26').replace(':','%3a') for x in temp_include)

    if parsed_args.exclude is not None:
        temp_exclude = []
        for tag in parsed_args.exclude:
            temp_exclude.append(urllib.parse.quote(tag))
        exclude_tags = ''.join('-%s+' % x.replace('&', '%26').replace(':','%3a') for x in temp_exclude)
    else:
        exclude_tags = ''

    if parsed_args.filter:
        filtered_tags = {item: 1 for item in parsed_args.filter}

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
    }

    return scrapeler_args

def expand_response_files(raw_args):
    expanded_args = ""
    for arg in raw_args:
        if arg.startswith('@'):
            response_file = open(arg[1:], 'r')
            arg_to_add = response_file.readline()
        else:
            arg_to_add = arg

        expanded_args = expanded_args.strip() + " " + arg_to_add

    return expanded_args


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

            # this logic is fucked but I don't care
            extension = current_img.split('?')[0][-5:].split('.')[1]
            # image file path always starts life as a .jpg
            image_file_path = image_file_path[:-3] + extension

            if not os.path.exists(image_file_path):
                delay = 4 + random.uniform(2, 4)
                time.sleep(delay)
                ret = save_image(referer_id, current_img, image_file_path)
            else:
                print('{0} skipped: Already saved.'.format(current_img))
        except AttributeError as ae:
            # TODO Refactor this to be less redundant.
            img_tag = soup.find('source')
            current_img = img_tag.attrs['src']
            image_file_path = image_file_path[:-3] + 'webm'
            if not os.path.exists(image_file_path):
                delay = 4 + random.uniform(3, 4)
                time.sleep(delay)
                ret = save_image(referer_id, current_img, image_file_path)
            else:
                print('{0} skipped: Already saved.'.format(current_img))

        except Exception as e:
            print('Unhandled exception: {}'.format(e))
    return ret


def save_image(referer_id, current_img, save_to):
    with requests.Session() as sess:
        response = sess.get(current_img, data=None, stream=True,
                            headers={
                                'User-Agent': UserAgent().firefox,
                                'Referer': referer_id,
                            }
        )
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
        scrape_url = main_url_base.format(url_tags=url_tags, pid=str(42 * (page-1)))
        print('\nScraping: {0}, (page {1})'.format(scrape_url, page))

        scrape_soup = get_soup(scrape_url)
        results = scrape_soup.findAll('img', class_='preview')

        found_imgs += len(results)
        if len(results) < 42:
            keep_scraping = False

        print('{0} results on page\n'.format(len(results)))

        for result in results:
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
                if save_current:
                    image_file_path = "{directory}\\{fn}".format(directory=scrapeler_args['scrape_save_directory'],
                                                                 fn=img_fn)
                    delay = scrapeler_args['base_delay'] + random.uniform(0, 2)
                    time.sleep(delay)
                    saved_imgs += route_through_subpage(scrape_url, referer_base.format(refer_id), image_file_path)
                elif not save_current:
                    print('{0} was filtered. Matched: {1}.'.format(referer_base.format(refer_id), filter_reasons[:3]))

                    # todo if you scrape and find this tag: <title>Gelbooru - Intermission Ad</title>
                    # wait 15 seconds then try the page again

        if not scrapeler_args['scanonly']:
            total_saved_imgs += saved_imgs
            print('{0} images saved for page {1}. ({2} images saved in total.)'.format(saved_imgs, page,
                                                                                       total_saved_imgs))

        page += 1
        if -1 < final_page == page:
            print('Page limit ({0}) was reached. Stopping.'.format(final_page))
            keep_scraping = False

        if scrapeler_args['short'] and not scrapeler_args['scanonly'] and saved_imgs == 0:
            print('No images saved with shortcircuit on. Stopping.')
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

    perform_gelbooru_scrape(scrapeler_args)
    if scrapeler_args['batch']:
        batch_file = scrapeler_args['batch']
        for command in batch_file:
            try:
                delay = random.uniform(300, 450)
                print('Sleeping for {0} seconds between commands.'.format(delay))
                time.sleep(delay)
                perform_gelbooru_scrape(parse_scrapeler_args(command))
            except Exception as ex:
                print('Unhandled exception {e} occured during command {c}'.format(e=ex, c=command))
        print('Finished.')

if __name__ == '__main__':
    main()

