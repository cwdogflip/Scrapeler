# -*- coding: utf-8 -*-

import os
import random
import re
import time

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent


def get_soup(url):
    with requests.Session() as sess:
        response = sess.get(url, data=None, headers={
            'User-Agent': UserAgent().random,
            'Accept': '''text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8''',
            'Connection': 'keep-alive',
        })

        if response.status_code == 200:
            return BeautifulSoup(response.text, "html5lib")
        elif response.status_code >= 500:
            return None


def route_through_subpage(directory_page, subpage_id, image_file_path):
    ret = 0
    with requests.Session() as sess:
        response = sess.get(subpage_id, data=None, headers={
            'User-Agent': UserAgent().firefox,
            'Referer': directory_page,
        })

    if response.status_code == 200:
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

                extension = current_img.split('?')[0][-5:].split('.')[1]
                image_file_path = image_file_path[:-3] + extension
            else:
                img_tag = soup.find('source')
                current_img = img_tag.attrs['src']
                image_file_path = image_file_path[:-3] + 'webm'

            if not os.path.exists(image_file_path):
                delay = 4 + random.uniform(3, 4)
                time.sleep(delay)
                ret = save_image(subpage_id, current_img, image_file_path)
        except Exception as e:
            pass
    return ret


def save_image(referencing_page, current_img, save_to):
    with requests.Session() as sess:
        response = sess.get(current_img, data=None, stream=True,
                            headers={
                                'User-Agent': UserAgent().firefox,
                                'Referer': referencing_page,})

        if response.status_code >= 400:
            return 0

        try:
            if response.status_code == 200:
                with open(save_to, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=1024):
                        if chunk:  # filter out keep-alive new chunks
                            f.write(chunk)
                    return 1

        except Exception as e:
            return 0


def scrape_booru(waifu_name, scrape_save_directory):
    main_url_base = \
        r'http://gelbooru.com/index.php?page=post&s=list&tags={waifu}+rating%3aexplicit+sort%3ascore%3adesc&pid={pid}'
    referer_base = r'http://gelbooru.com/index.php?page=post&s=view&id={0}'
    id_regex = re.compile(r'(?<=thumbnail_)([\da-f]*\.jpg|\.png|\.gif)')
    referer_regex = re.compile(r'\?[\da-f]*')
    total_saved_imgs = 0

    page = 1
    more_pages = True
    while total_saved_imgs < 52 and more_pages:
        time.sleep(4 + random.uniform(0, 2))

        scrape_url = main_url_base.format(waifu=waifu_name, pid=str(42 * (page - 1)))
        scrape_soup = get_soup(scrape_url)
        results = scrape_soup.findAll('img', class_='preview')

        if len(results) < 42:
            more_pages = False

        for num, result in enumerate(results):
            img_fn = id_regex.search(result.attrs['src']).group(1)
            refer_id = referer_regex.search(result.attrs['src']).group(0)[1:]

            image_file_path = "{directory}\\{fn}".format(
                directory=scrape_save_directory,
                fn=img_fn
            )
            time.sleep(2 + random.uniform(0, 2))
            total_saved_imgs += route_through_subpage(scrape_url, referer_base.format(refer_id), image_file_path)
            if total_saved_imgs >= 52:
                more_pages = False
                break

        page += 1

    return total_saved_imgs


if __name__ == '__main__':
    exit('This is the game jam scrapeler.')
