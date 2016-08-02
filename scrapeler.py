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

from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from io import BytesIO
from PIL import Image

main_url_base = r'http://gelbooru.com/index.php?page=post&s=list&tags={url_tags}&pid={pid}'
grab_url_base = r'http://gelbooru.com//images/{0}/{1}/{2}'
referer_base = r'http://gelbooru.com/index.php?page=post&s=view&id={0}'

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
            print (response.status_code)
            return None



def parse_scrapeler_args():
    scrapeler_args = {}
    
    parser = argparse.ArgumentParser(description='Scrape a booru-style image database. At least one tag is required to scrape. It\'s recommended you give one specific tag to avoid flooding yourself with images you don\' want. Scrapeler will not scrape more than 100 pages at once.')
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
    parser.add_argument("--patient", default=False, action='store_true', help='If on, the minimum delay between url requests will be increased by 3 seconds. Better if you have a slow connection') 
    parser.add_argument("--aggressive", default=False, action='store_true', help='If on, the minimum delay between url requests will be reduced by 2 seconds. Don\'t get banned!')

    args = parser.parse_args()

    if args.dir is not None:
        directory = args.dir
        save_path= os.path.abspath(directory)
    else:
        directory = datetime.datetime.now().strftime('{0} %Y%m%d_%H%M').format(args.tags[0])
        save_path= os.path.abspath(directory)

    if not os.path.exists(save_path):
        os.mkdir(save_path)

    temp_include = []
    for tag in args.tags:
        temp_include.append(urllib.parse.quote(tag))

    include_tags= ''.join('%s+' % x.replace('&', '%26') for x in temp_include)

    if args.exclude is not None:
        temp_exclude = []
        for tag in args.exclude:
            temp_exclude.append(urllib.parse.quote(tag))
            
        exclude_tags = ''.join('-%s+' % urllib.parse.quote(x) for x in temp_exclude)
    else:
        exclude_tags = ''

    base_delay = 3
    if args.patient: base_delay += 3
    if args.aggressive: base_delay -= 2
    

    scrapeler_args['tags'] = args.tags
    scrapeler_args['exclude'] = args.exclude
    scrapeler_args['url_tags'] = (include_tags + exclude_tags)[:-1]
    scrapeler_args['scrape_save_directory'] = save_path
    scrapeler_args['page'] = args.page
    scrapeler_args['kwfile'] = args.keywordfile
    scrapeler_args['kwcount'] = args.kwcount if args.kwcount >= -1 else 0
    scrapeler_args['scanonly'] = args.scanonly
    scrapeler_args['base_delay'] = base_delay
    scrapeler_args['sleep'] = args.sleep
    if args.randompagelimit:
        scrapeler_args['pagelimit'] = _rand.randint(10,30)
    else:
        scrapeler_args['pagelimit'] = args.pagelimit if args.pagelimit > 0 else -1

    
    return scrapeler_args


def save_image(save_to, current_img, referer_id):
    with requests.Session() as sess:
        response = sess.get(current_img, data=None, headers={
                            'User-Agent': UserAgent().firefox,
                            'Referer': referer_id,
                            })
        if not response.status_code == 404:
            b = BytesIO(response.content)
            try:
                image = Image.open(b)
                image.save(save_to)
                image.close()
                print(current_img)
            except Exception as e:
                pass

        else: # Probably another file extension
            alt_extensions = ('.jpeg', '.png', '.gif', '.webm',)
            base_url = current_img.replace('.', '__dot__', 1)
            base_url = base_url.split('.')[0]
            base_url = base_url.replace('__dot__', '.', 1)

            base_save = save_to.split('.')[0]
            for ext in alt_extensions:
                delay = _rand.uniform(2,4)
                time.sleep(delay)
                try:
                    current_img = base_url+ext
                    response = sess.get(current_img, data=None, headers={
                                    'User-Agent': UserAgent().random,
                                    'Referer': referer_id,
                                    })

                    if not response.status_code == 404:
                        if not os.path.isfile(base_save+ext):
                            b = BytesIO(response.content)
                            image = Image.open(b)
                            image.save(base_save+ext)
                            image.close()
                            print(current_img)
                        break
                        
                except Exception as e:
                    pass


def scrape_booru(scrapeler_args):

    id_regex = re.compile(r'(?<=thumbnail_)([\da-f]*\.jpg|\.png|\.gif)')
    referer_regex = re.compile(r'\?[\da-f]*')
    related_tags = {}
    page = scrapeler_args['page']
    url_tags = scrapeler_args['url_tags']
    if scrapeler_args['sleep']:
        next_sleep = _rand.randint(3600, 10800)  # 1 to 3 hours til sleeping.
        print('Will sleep in {0} seconds'.format(next_sleep))
    keep_scraping = True
    
    while keep_scraping:
        timestamp = datetime.datetime.now()
        delay = scrapeler_args['base_delay'] + _rand.uniform(2,4)
        time.sleep(delay)
        scrape_url = main_url_base.format(url_tags=url_tags ,pid=str(42* (page-1)))
        scrape_soup = get_soup(scrape_url)
        print('Scraping: {0}, (page {1})'.format(scrape_url, page))
        results = scrape_soup.findAll('img', class_='preview')
        if len(results) < 42:
            keep_scraping = False
        
        for result in results:
            if scrapeler_args['kwcount'] != 0:
                for tag in result.attrs['title'].split():
                    if tag in related_tags:
                        related_tags[tag] += 1
                    else:
                        related_tags[tag] = 1

            img_fn = id_regex.search(result.attrs['src']).group(1)
            refer_id = referer_regex.search(result.attrs['src']).group(0)
            current = grab_url_base.format(img_fn[:2], img_fn[2:4], img_fn)
            # Check if image is already saved before scraping it?
            image_file_path = "{directory}\\{fn}".format(directory=scrapeler_args['scrape_save_directory'],
                                                         fn=img_fn)

            if not os.path.exists(image_file_path) and not scrapeler_args['scanonly']:
                delay = scrapeler_args['base_delay'] + _rand.uniform(0,2)
                time.sleep(delay)
                save_image(image_file_path, current, referer_base.format(refer_id))

            # todo if you scrape and find this tag: <title>Gelbooru - Intermission Ad</title>
            # wait 15 seconds then try the page again
        if 0 < scrapeler_args['pagelimit'] < scrapeler_args['page'] - page:
            keep_scraping = False

        page+= 1
        if keep_scraping and scrapeler_args['sleep']:
            rn = datetime.datetime.now()
            if rn - timestamp > datetime.timedelta(seconds=next_sleep):
                delay = _rand.randint(7200, 14400) + _rand.uniform(0,1)  #2 to 4 hours of sleeping.
                while datetime.datetime.now() < rn + datetime.timedelta(seconds=delay):
                    time.sleep(120)
                    print('Sleeping...')
                timestamp = datetime.datetime.now()
                next_sleep = _rand.randint(3600, 10800)  # 1 to 3 hours til sleeping.
                print('Will sleep in {0} seconds'.format(next_sleep))

    return related_tags
    
    
def main():
    scrapeler_args = parse_scrapeler_args()
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
                if kwcount > 0:
                    kwf.write('\r\nWhich found the following keyword list:\r\n')
                    for tag in sorted_related_tags[:kwcount]:
                        kwf.write('{tag} : {count}\r\n'.format(tag=tag, count=related_tags[tag]))
    else:
        for tag in sorted_related_tags[:kwcount]:
            print(tag, related_tags[tag])


if __name__ == '__main__':
    main()

