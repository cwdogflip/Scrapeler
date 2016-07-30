import argparse


def main():
    parser = argparse.ArgumentParser(description='Scrape a booru-style image database')
    parser.add_argument("tags", type=str, help="Enter the tags you want to scrape.", 
                         nargs='*')

    args = parser.parse_args()
    print(args)
    




if __name__ == '__main__':
	main()

