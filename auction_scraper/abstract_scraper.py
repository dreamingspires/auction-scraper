from urllib.parse import urljoin, urlparse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os.path
import validators
import requests
from bs4 import BeautifulSoup
import unicodedata
import traceback

from auction_scraper.abstract_models import Base, BaseAuction, BaseProfile

class SearchResult():
    def __init__(self, name, uri):
        self.name = name
        self.uri = uri

class AbstractAuctionScraper():
    # Defined by subclass
    auction_table = None
    profile_table = None
    base_uri = None
    base_auction_suffix = None
    base_profile_suffix = None
    base_search_suffix = None

    def __init__(self, db_path, data_location, base_uri=None, \
            base_auction_suffix=None, base_profile_suffix=None, \
            base_search_suffix = None, auction_save_path=None, \
            profile_save_path=None, search_save_path=None):
        if base_auction_suffix is not None:
            self.base_auction_suffix = base_auction_suffix
        if base_profile_suffix is not None:
            self.base_profile_suffix = base_profile_suffix
        if base_search_suffix is not None:
            self.base_search_suffix = base_search_suffix

        self.base_auction_uri = urljoin(self.base_uri, self.base_auction_suffix)
        self.base_profile_uri = urljoin(self.base_uri, self.base_profile_suffix)
        self.base_search_uri = urljoin(self.base_uri, self.base_search_suffix)

        self.data_location = data_location
        
        # TODO: Convert into pathlib.path objects
        self.auction_save_path = auction_save_path
        self.profile_save_path = profile_save_path
        self.search_save_path = search_save_path

        # Some method of choosing what the saved html file names are
        self.auction_save_name = 'auction-{}.html'
        self.profile_save_name = 'profile-{}.html'
        self.search_save_name = 'search-{}.html'

        if self.auction_table is None or self.profile_table is None:
            raise ValueError('self.auction_table and self.profile_table must be set in the __init__ method of a subclass of AbstractAuctionScraper')

        # Define the application base directory
        self.engine = create_engine('sqlite:///' + os.path.abspath(db_path), \
            echo=True)
        self.Session = sessionmaker(bind=self.engine)

        # Create the database tables
        Base.metadata.create_all(self.engine)

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
        return norm


    def _get_page(self, uri, resolve_iframes=False):
        """
        Requests the page from uri and returns a bs4 soup.
        If resolve_iframes, resolves all iframes in the page.
        """
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

    def scrape_auction(self, auction, save_page=False):
        """
        Scrapes an auction page, specified by either a unique auction ID
        or a URI.  Returns an auction model containing the scraped data.
        If specified by auction ID, constructs the URI using self.base_uri.
        If self.page_save_path is set, writes out the downloaded pages to disk at
        the given path according to the naming convention specified by
        self.auction_save_name.
        """
        if not isinstance(auction, str):
            raise ValueError('auction must be a str')

        auction_uri = self.base_auction_uri.format(auction) \
            if not validators.url(auction) else auction

        if save_page and not self.auction_save_path:
            raise ValueError(
                "Can't save page: auction_save_path not specified on scraper initialisation")

        # Get the auction page
        # auction_id should be returned in case it was specified by uri
        auction, html = self._scrape_auction_page(auction_uri)

        # Save if required
        if save_page:
            name = self.auction_save_name.format(auction_id)
            with open(self.auction_save_path.joinpath(name)) as f:
                f.write(soup.prettify())

        # TODO: save out images if required, updating the image paths field
        # as required

        return auction

    def scrape_profile(self, profile, save_page=False):
        """
        Scrapes a profile page, specified by either a unique profile ID
        or a URI.  Returns an profile model containing the scraped data.
        If specified by profile ID, constructs the URI using self.base_uri.
        If self.page_save_path is set, writes out the downloaded pages to disk at
        the given path according to the naming convention specified by
        self.profile_save_name.
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
            name = self.profile_save_name.format(profile_id)
            with open(self.profile_save_path.joinpath(name)) as f:
                f.write(soup.prettify())

        return profile

    # TODO: implement save_page
    def scrape_search(self, query_string, n_results=None, save_page=False):
        """
        Scrapes a search page, specified by either a query_string and n_results,
        or by a unique URI.
        If specified by query_string, de-paginates the results and returns up
        to n_results results.  If n_results is None, returns all results.
        If specified by a search_uri, returns just the results on the page.
        """

        results = {}
        n_page = 1
        # De-paginate the search results
        while n_results is None or len(results) < n_results:
            uri = self._generate_search_uri(query_string, n_page)
            n_res = len(results)
            res, html = self._scrape_search_page(uri)
            # TODO: save the html page here if required
            results = {**results, **res}
            if len(results) == n_res:
                break
            n_page += 1

        # Cut down the number of results to n_results
        if n_results is not None:
            while len(results) > n_results:
                results.popitem()

        return results

    def scrape_auction_to_db(self, auction, save_page=False):
        auction = self.scrape_auction(auction, save_page)
        session = self.Session()
        session.merge(auction)
        session.commit()
        return auction
    
    def scrape_profile_to_db(self, profile, save_page=False):
        profile = self.scrape_profile(profile, save_page)
        session = self.Session()
        session.merge(profile)
        session.commit()
        return profile

    def scrape_search_to_db(self, query_strings, n_results=None, save_page=False):
        if isinstance(query_strings, str):
            query_strings = [query_strings]

        # Get search results, deduplicating across queries through dict merging
        results = {}
        for query_string in query_strings:
            results = {**results, \
                    **self.scrape_search(query_string, n_results)}

        scraped_profile_ids = set()
        exceptions = []
        for auction_id, search in results.items():
            try:
                print('Scraping auction url {}'.format(search.uri))
                auction = self.scrape_auction_to_db(search.uri, save_page)
                profile_id = auction.seller_id

                if profile_id is not None and profile_id not in scraped_profile_ids:
                    print('Scraping profile {}'.format(profile_id))
                    self.scrape_profile_to_db(profile_id, save_page)
                    scraped_profile_ids.add(profile_id)

            except Exception as e:
                # TODO: instead of colouring the output, we should return a 
                # dict mapping the auctions and profiles that failed to scrape
                # to the error messages that were output
                print(f'Error processing auction {auction_id}')
                print(traceback.format_exc())

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
