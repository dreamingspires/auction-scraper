#   Copyright (c) 2020 Dreaming Spires
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.

from urllib.parse import urljoin, urlparse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os.path
import validators
import requests
from bs4 import BeautifulSoup
import unicodedata
import traceback
from pathlib import Path
from termcolor import colored
import json
import time

from auction_scraper.abstract_models import Base

# From https://stackoverflow.com/questions/18092354/python-split-string-without-splitting-escaped-character#21107911
def _escape_split(s, delim):
    i, res, buf = 0, [], ''
    while True:
        j, e = s.find(delim, i), 0
        if j < 0:  # end reached
            return res + [buf + s[i:]]  # add remainder
        while j - e and s[j - e - 1] == '\\':
            e += 1  # number of escapes
        d = e // 2  # number of double escapes
        if e != d * 2:  # odd number of escapes
            buf += s[i:j - d - 1] + s[j]  # add the escaped char
            i = j + 1  # and skip it
            continue  # add more to buf
        res.append(buf + s[i:j - d])
        i, buf = j + len(delim), ''  # start after delim

class UnexpectedPageError(Exception):
    def __init__(self, page):
        self.message = 'Failed to parse page due to unexpected contents. This could be due to the scraper being blocked by anti-scraper measures.'
        self.page = page

    def __str__(self):
        return f'{self.message}'

class SearchResult():
    def __init__(self, name, uri):
        self.name = name
        self.uri = uri

class AbstractAuctionScraper():
    # Defined by subclass
    auction_table = None
    profile_table = None
    base_uri = None
    auction_suffix = None
    profile_suffix = None
    search_suffix = None
    backend_name = None
    cooldown = None
    cooldown_timestamp = None

    def __init__(self, db_path, data_location=None, base_uri=None, \
            auction_suffix=None, profile_suffix=None, \
            search_suffix = None, auction_save_path=None, \
            profile_save_path=None, search_save_path=None, \
            image_save_path=None, verbose=False, cooldown=0, **_):
        self.verbose = verbose

        if auction_suffix is not None:
            self.auction_suffix = auction_suffix
        if profile_suffix is not None:
            self.profile_suffix = profile_suffix
        if search_suffix is not None:
            self.search_suffix = search_suffix

        if base_uri is not None:
            self.base_uri = base_uri
        self.base_auction_uri = urljoin(self.base_uri, self.auction_suffix)
        self.base_profile_uri = urljoin(self.base_uri, self.profile_suffix)
        self.base_search_uri = urljoin(self.base_uri, self.search_suffix)

        self.cooldown = cooldown

        # Configure default data locations
        if data_location is not None:
            data_location = Path(data_location)
            self.auction_save_path = data_location \
                .joinpath(self.backend_name).joinpath('auctions')
            self.profile_save_path = data_location \
                .joinpath(self.backend_name).joinpath('profiles')
            self.search_save_path = data_location \
                .joinpath(self.backend_name).joinpath('searches')
            self.image_save_path = data_location \
                .joinpath(self.backend_name).joinpath('images')
        else:
            self.auction_save_path = None
            self.profile_save_path = None
            self.search_save_path = None
            self.image_save_path = None

        # Override specified data locations
        if auction_save_path is not None:
            self.auction_save_path = Path(auction_save_path)
        if profile_save_path is not None:
            self.profile_save_path = Path(profile_save_path)
        if search_save_path is not None:
            self.search_save_path = Path(search_save_path)
        if image_save_path is not None:
            self.image_save_path = Path(image_save_path)

        # Create data locations if don't exist
        if self.auction_save_path is not None:
            self.auction_save_path.mkdir(parents=True, exist_ok=True)
        if self.profile_save_path is not None:
            self.profile_save_path.mkdir(parents=True, exist_ok=True)
        if self.search_save_path is not None:
            self.search_save_path.mkdir(parents=True, exist_ok=True)
        if self.image_save_path is not None:
            self.image_save_path.mkdir(parents=True, exist_ok=True)

        # Some method of choosing what the saved html file names are
        self.auction_save_name = 'auction-{}.html'
        self.profile_save_name = 'profile-{}.html'
        self.search_save_name = 'search-{}-{}.html'

        if self.auction_table is None or self.profile_table is None:
            raise ValueError('self.auction_table and self.profile_table must be set in the __init__ method of a subclass of AbstractAuctionScraper')

        # Define the application base directory
        self.engine = create_engine('sqlite:///' + os.path.abspath(db_path), \
            echo=verbose, connect_args={'timeout': 20})
        self.Session = sessionmaker(bind=self.engine)

        # Create the database tables
        Base.metadata.create_all(self.engine)

    def _download_images(self, image_urls, auction_id):
        # backend-name  instead of name prefix
        image_paths = []
        for url in image_urls:
            name = f'{self.backend_name}_{auction_id}_' + \
                '_'.join(urlparse(url).path.split('/'))
            path = self.image_save_path.joinpath(name).resolve()
            image_paths.append(path)

            if not path.is_file():
                r = requests.get(url)
                if not r.ok:
                    print(colored('Could not find page: {}'.format(url), 'red'))
                with open(path, 'wb') as f:
                    f.write(r.content)

        return image_paths

    def _normalise_text(self, text):
        """
        Normalise blocks of text, removing unneccesary whitespace and
        normalising unicode-encoded characters.
        """
        # Strips multiple groups of c from s
        def strip_multiple(s, c):
            return c.join(filter(None, s.split(c)))

        norm = unicodedata.normalize("NFKD", text)
        norm = '\n'.join(filter(None, filter(lambda e: not e.isspace(), norm.split('\n'))))
        norm = strip_multiple(norm, ' ')
        norm = norm.encode('ascii', errors='ignore').decode('unicode-escape')
        return norm


    def _get_page(self, uri, resolve_iframes=False):
        """
        Requests the page from uri and returns a bs4 soup.
        If resolve_iframes, resolves all iframes in the page.
        """
        now = time.time()
        if self.cooldown_timestamp is not None and \
                self.cooldown_timestamp + self.cooldown > now:
            sleep_time = self.cooldown - (now - self.cooldown_timestamp)
            if self.verbose:
                print('Awaiting cooldown expiry in {}s'.format( \
                    int(sleep_time)))
            time.sleep(sleep_time)
        self.cooldown_timestamp = time.time()

        r = requests.get(uri)
        if not r.ok:
            raise ValueError('The requested page could not be found')
        soup = BeautifulSoup(r.text, 'html.parser')
        if resolve_iframes:
            for iframe in soup.find_all('iframe'):
                try:
                    src = iframe['src']
                except KeyError:
                    continue

                ir = requests.get(src)
                if not ir.ok:
                    continue
                iframe_soup = BeautifulSoup(ir.text, 'html.parser')
                iframe.append(iframe_soup)
        return soup

    def _get_json(self, uri):
        """
        Requests the page from uri and returns a json object.
        If resolve_iframes, resolves all iframes in the page.
        """
        r = requests.get(uri)
        if not r.ok:
            raise ValueError('The requested page could not be found')
        return json.loads(r.text)

    def scrape_auction(self, auction, save_page=False, save_images=False):
        """
        Scrapes an auction page, specified by either a unique auction ID
        or a URI.  Returns an auction model containing the scraped data.
        If specified by auction ID, constructs the URI using self.base_uri.
        If self.page_save_path is set, writes out the downloaded pages to disk at
        the given path according to the naming convention specified by
        self.auction_save_name.
        Returns a BaseAuction
        """
        if not isinstance(auction, str):
            raise ValueError('auction must be a str')

        auction_uri = self.base_auction_uri.format(auction) \
            if not validators.url(auction) else auction

        if save_page and not self.auction_save_path:
            raise ValueError(
                "Can't save page: data-location not specified on scraper initialisation")
        if save_images and not self.auction_save_path:
            raise ValueError(
                "Can't save images: data-location not specified on scraper initialisation")

        # Get the auction page
        # auction_id should be returned in case it was specified by uri
        auction, html = self._scrape_auction_page(auction_uri)

        # Save if required
        if save_page:
            name = self.auction_save_name.format(auction.id)
            with open(self.auction_save_path.joinpath(name), 'w') as f:
                f.write(html)

        # Save images if required, updating image_paths
        if save_images:
            new_image_paths = list(map(str, self._download_images(filter(None, auction.image_urls.split(' ')), auction.id)))
            if auction.image_paths is not None:
                existing_image_paths = _escape_split( \
                    auction.image_paths, ':')
            else:
                existing_image_paths = []

            auction.image_paths = ':'.join(list(set(new_image_paths).union( \
                existing_image_paths)))

        return auction


    def scrape_profile(self, profile, save_page=False):
        """
        Scrapes a profile page, specified by either a unique profile ID
        or a URI.  Returns an profile model containing the scraped data.
        If specified by profile ID, constructs the URI using self.base_uri.
        If self.page_save_path is set, writes out the downloaded pages to disk at
        the given path according to the naming convention specified by
        self.profile_save_name.
        Returns a BaseProfile
        """

        if not isinstance(profile, str):
            raise ValueError('profile must be a str')

        profile_uri = self.base_profile_uri.format(profile) \
            if not validators.url(profile) else profile

        if save_page and not self.profile_save_path:
            raise ValueError(
                "Can't save page: profile_save_path not specified on scraper initialisation")

        # Get the profile page
        profile, html = self._scrape_profile_page(profile_uri)

        # Save if required
        if save_page:
            name = self.profile_save_name.format(profile.id)
            with open(self.profile_save_path.joinpath(name), 'w') as f:
                f.write(html)

        return profile

    def scrape_search(self, query_string, n_results=None, save_page=False,
            save_images=False):
        """
        Scrapes a search page, specified by either a query_string and n_results,
        or by a unique URI.
        If specified by query_string, de-paginates the results and returns up
        to n_results results.  If n_results is None, returns all results.
        If specified by a search_uri, returns just the results on the page.
        Returns a dict {auction_id: SearchResult}
        """

        if save_page and not self.auction_save_path:
            raise ValueError(
                "Can't save page: data-location not specified on scraper initialisation")
        if save_images and not self.auction_save_path:
            raise ValueError(
                "Can't save images: data-location not specified on scraper initialisation")

        results = {}
        n_page = 1
        # De-paginate the search results
        while n_results is None or len(results) < n_results:
            uri = self._generate_search_uri(query_string, n_page)
            n_res = len(results)
            if self.verbose:
                print(f'Scraping search page with uri {uri}')
            res, html = self._scrape_search_page(uri)

            # Save the html page here if required
            if save_page:
                name = self.search_save_name.format(query_string, n_page)
                with open(self.search_save_path.joinpath(name), 'w') as f:
                    f.write(html)

            results = {**results, **res}
            if len(results) == n_res:
                break
            n_page += 1

        # Cut down the number of results to n_results
        if n_results is not None:
            while len(results) > n_results:
                results.popitem()

        return results

    def scrape_auction_to_db(self, auction, save_page=False, save_images=False):
        """
        Scrape an auction page, writing the resulting auction to the database.
        Returns a BaseAuction
        """
        auction = self.scrape_auction(auction, save_page, save_images)
        session = self.Session()
        try:
            session.merge(auction)
            session.commit()
        except Exception as e:
            session.close()
            raise e
        return auction

    def scrape_profile_to_db(self, profile, save_page=False):
        """
        Scrape a profile page, writing the resulting profile to the database.
        Returns a BaseProfile
        """
        profile = self.scrape_profile(profile, save_page)
        session = self.Session()
        try:
            session.merge(profile)
            session.commit()
        except Exception as e:
            session.close()
            raise e
        return profile

    def scrape_search_to_db(self, query_strings, n_results=None, \
            save_page=False, save_images=False, cooldown=0):
        """
        Scrape a set of query_strings, writing the resulting auctions and profiles
        to the database.
        Returns a tuple ([BaseAuction], [BaseProfile])
        """
        if isinstance(query_strings, str):
            query_strings = [query_strings]

        # Get search results, deduplicating across queries through dict merging
        results = {}
        for query_string in query_strings:
            print(f'Scraping query string {query_string}')
            # Retry the search three times, to deal with transient errors
            #raise NotImplementedError
            for i in range(3):
                try:
                    results = {**results, \
                            **self.scrape_search(query_string, n_results, save_page,
                                save_images)}
                except Exception as e:
                    if i == 2:
                        raise e
                    else:
                        print(e)
                        time.sleep(1)
                else:
                    break

        scraped_profile_ids = set()
        exceptions = []
        auctions = []
        profiles = []
        for auction_id, search in results.items():
            try:
                print('Scraping auction url {}'.format(search.uri))
                auction = self.scrape_auction_to_db(search.uri, save_page, \
                    save_images)
                auctions.append(auction)
                profile_id = auction.seller_id

                if profile_id is not None and profile_id not in scraped_profile_ids:
                    print('Scraping profile {}'.format(profile_id))
                    profile = self.scrape_profile_to_db(profile_id, save_page)
                    profiles.append(profile)
                    scraped_profile_ids.add(profile_id)

            except Exception as e:
                exceptions.append(e)
                print(f'Error processing auction {auction_id}')
                print(traceback.format_exc())

        if exceptions:
            raise Exception(exceptions)

        return auctions, profiles

    def _scrape_auction_page(self, uri):
        raise NotImplementedError('Subclass implements this')

    def _scrape_profile_page(self, uri):
        raise NotImplementedError('Subclass implements this')

    def _generate_search_uri(self, query_string, n_page):
        """
        Returns a uri for the n_page page with the query_string parameter
        """
        raise NotImplementedError('Subclass implements this')

    def _scrape_search_page(self, uri):
        """
        Returns a dict mapping unique auction IDs to auction search objects
        """
        raise NotImplementedError('Subclass implements this')
