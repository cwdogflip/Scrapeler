# -*- coding: utf-8 -*-

import argparse
import re
import os
import datetime
import time
import random as _rand
import urllib
import requests
import shutil

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
        
def main():
    parser = argparse.ArgumentParser(description='Scrape a booru-style image database. At least one tag is required to scrape. It\'s recommended you give one specific tag. Scrapeler will not scrape more than 100 pages at once.')
    parser.add_argument("tags", type=str,
                         help="Enter the tags you want to scrape.\nAt least 1 tag argument is required.", 
                         nargs='+',)
    parser.add_argument("-e", "--exclude", type=str, help="Enter tags you want to avoid (Optional)",
                        nargs='*', default=None)
    parser.add_argument("-d", "--dir", help="The directory you want the images saved to.", nargs = '?')
    parser.add_argument("-p", "--page", type=int, help="Page you want to start the scraping on", default=1, nargs='?')
    parser.add_argument("-c", "--kwcount", type=int, default=25,
                        help="The number of counted keywords reported. Defaults to 25. If this is 0, Scrapeler will not count keywords. Set this to -1 to grab all related keywords.")
    parser.add_argument("-f", "--keywordfile", default=False, action='store_true',
                        help="Whether or not to store keyword counts in a file. If not specified, Scrapeler will report to the console.")
    
    args = parser.parse_args()
    if args.dir is not None:
        directory = args.dir
        if directory[:1] in ('/', '\\'): # Stop them from putting it in the fucking C:\whatever
            directory = directory[1:]
        save_path= os.path.abspath(directory)
    else:
        directory = datetime.datetime.now().strftime('{0} %Y%m%d_%H%M').format(args.tags[0])
        save_path= os.path.abspath(directory)

    if not os.path.exists(save_path):
        os.mkdir(save_path)

    print('\nArguments parsed as:')
    print('Include tags:', args.tags)
    print('Exclude tags:', args.exclude)
    print('Save images to:', save_path)
    print('Start on page:', args.page)
    print(args)

    id_regex = re.compile(r'(?<=thumbnail_)([\da-f]*\.jpg|\.png|\.gif)')
    referer_regex = re.compile(r'\?[\da-f]*')
    include_tags= ''.join('%s+' % x for x in args.tags)[:-1]
    if args.exclude is not None:
        exclude_tags = ''.join('+-%s' % x for x in args.exclude)
    else:
        exclude_tags = ''

    url_tags = include_tags + exclude_tags
    keep_scraping = True
    related_tags = {}
    page = args.page
    # delay = .2
    while keep_scraping:
        delay = 5 + _rand.uniform(0,2)
        time.sleep(delay)
        scrape_url = main_url_base.format(url_tags=url_tags,pid=str(42* (page-1)))
        scrape_soup = get_soup(scrape_url)
        print(scrape_url)
        results = scrape_soup.findAll('img', class_='preview')
        if len(results) > 0:
            for result in results:
                delay = 3 + _rand.uniform(0,2)
                time.sleep(delay)

                if args.kwcount != 0:
                    for tag in result.attrs['alt'].split():
                        if tag not in args.tags:
                            if tag in related_tags:
                                related_tags[tag] += 1
                            else:
                                related_tags[tag] = 1
                img_fn = id_regex.search(result.attrs['src']).group(1)
                refer_id = referer_regex.search(result.attrs['src']).group(0)
                current = grab_url_base.format(img_fn[:2],img_fn[2:4],img_fn)
                # Check if image is already saved before scraping it?
                save_directory = "{save_to}\\{fn}".format(save_to=save_path, fn=img_fn)
                
                with requests.Session() as sess:
                    response = sess.get(current, data=None, headers={
                                        'User-Agent': UserAgent().firefox,
                                        'Referer': referer_base.format(refer_id),
                                        })
                    if not response.status_code == 404:
                        if not os.path.exists(save_directory):
                            try:
                                b = BytesIO(response.content)
                                image = Image.open(b)
                                image.save(save_directory)
                                image.close()
                            except OSError as e:
                                pass
                    else: # Probably another file name
                        alt_extensions = ('png', 'jpeg')
                        for alt in alt_extensions:
                            try:
                                current = current[:-3]+alt
                                response = sess.get(current, data=None, headers={
                                                'User-Agent': UserAgent().firefox,
                                                'Referer': referer_base.format(refer_id),
                                                })
                                if not response.status_code == 404:
                                    if not os.path.exists(save_directory):
                                            b = BytesIO(response.content)
                                            image = Image.open(b)
                                            image.save(save_directory)
                                            image.close()
                                            break
                                
                            except OSError as e:
                                pass
                print(current)

            # if you scrape and find this tag: <title>Gelbooru - Intermission Ad</title>
            # wait 15 seconds then try the page again
            page+= 1
            if page - args.page > 100:
                keep_scraping = False
        else:
            keep_scraping = False

    sorted_related_tags = sorted(related_tags, key=related_tags.get, reverse=True)
    if args.kwcount == -1:
        args.kwcount = len(sorted_related_tags)
    if args.keywordfile:
        with open(save_path + 'keywords.txt', 'a') as kwf:
                for tag in sorted_related_tags[:args.kwcount]:
                    kwf.write('{0} : {1}'.format(tag, related_tags[tag]))
    else:
        for tag in sorted_related_tags[:args.kwcount]:
            print(tag, related_tags[tag])


if __name__ == '__main__':
    main()

