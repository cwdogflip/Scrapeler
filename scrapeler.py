import argparse
import re

main_url_base = 'http://gelbooru.com/index.php?page=post&s=list&tags={url_tags}&pid={pid}'
grab_url_base = 'http://gelbooru.com/images/{0}/{1}/{2}'

def main():
    parser = argparse.ArgumentParser(description='Scrape a booru-style image database')
    parser.add_argument("tags", type=str, help="Enter the tags you want to scrape.", 
                         nargs='+')

    args = parser.parse_args()
    print(args.tags)
    regex = re.compile(r'(?<=thumbnail_)([\da-f]*)')
    tag = '<img src="http://gelbooru.com/thumbnails/37/e1/thumbnail_37e16daa361fc4ac9f581869c3716281.jpg?3283608" alt=" 1girl animal_ears ... white_bikini " title=" 1girl animal_ears ... white_bikini  score:0 rating:safe" class="preview" style="" border="0">'
    img_fn = regex.search(tag).group(1)
    current = grab_url_base.format(img_fn[:2],img_fn[2:4],img_fn)
    print(current)

    url_tags= ''.join('%s+' % x for x in args.tags)[:-1]
    page = 1
    next_directory_url = main_url_base.format(url_tags=url_tags,pid='0')
    print(next_directory_url)


if __name__ == '__main__':
    main()

