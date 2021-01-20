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

from urllib.parse import urlparse, urljoin
from os import devnull
import sys
from slimit.parser import Parser
from slimit.visitors import nodevisitor
from slimit import ast
import re
from datetime import datetime
from pathlib import Path
from sqlalchemy_utils import Currency
import contextlib
import json

@contextlib.contextmanager
def silence_output():
    save_stdout = sys.stdout
    save_stderr = sys.stderr
    sys.stdout = open(devnull, 'w')
    sys.stderr = open(devnull, 'w')
    yield
    sys.stdout = save_stdout
    sys.stderr = save_stderr

from auction_scraper.abstract_scraper import AbstractAuctionScraper, \
    SearchResult, UnexpectedPageError
from auction_scraper.scrapers.liveauctioneers.models import \
    LiveAuctioneersAuction, LiveAuctioneersProfile

class LiveAuctioneersAuctionScraper(AbstractAuctionScraper):
    auction_table = LiveAuctioneersAuction
    profile_table = LiveAuctioneersProfile
    base_uri = 'https://www.liveauctioneers.com'
    auction_suffix = '/item/{}'
    profile_suffix = '/auctioneer/{}'
    search_suffix_default = '/search/?keyword={}&page={}'
    search_suffix_archive = '/search/?keyword={}&page={}&status=archive'
    search_suffix = None
    backend_name = 'liveauctioneers'

    def __init__(self, archive_search, **kwargs):
        self.search_suffix = self.search_suffix_archive if archive_search else self.search_suffix_default
        super().__init__(**kwargs)

    def __extract_data_json(self, soup):

        js = soup.body.find('script', attrs={'data-reactroot': True})
        if js is None:
            raise UnexpectedPageError(soup)
        js = js.string

        # bash embedded JavaScript into being valid JSON
        assert(js[:14]=='window.__data=' and js[-1:]==';')
        js = js[14:-1]
        js = js.replace('undefined', 'null')

        result = json.loads(js)
        return result

    def __address_from_seller(self, seller):
        address_strings = [seller.get('address'), seller.get('address2'), seller.get('city'), seller.get('country')]
        return '\n'.join(filter(None, address_strings))

    ### auction scraping

    def __parse_2021_auction_soup(self, soup, auction_id):

        def get_embedded_image_urls():
            imgs = soup.find_all('img', attrs= \
                {'class' : re.compile('Thumbnail__StyledThumbnailImage')})
            urls = []
            for i in imgs:
                urllist = i['src'].split('.')
                urllist[-1] = 'jpg'
                urls.append('.'.join(urllist))
            return urls

        json = self.__extract_data_json(soup)

        item = json['item']['byId'][str(auction_id)]
        item_detail = json['itemDetail']['byId'][str(auction_id)]
        bidding_info = json['biddingInfo']['byId'][str(auction_id)]
        catalog = json['catalog']['byId'][str(item['catalogId'])]

        # Extract additional auctioneer info
        seller_id = item['sellerId']
        seller = json['seller']['byId'][str(seller_id)]
        auctioneer = seller['name']
        location = self.__address_from_seller(seller)

        image_urls = ' '.join(get_embedded_image_urls())

        # Construct the auction object
        auction = LiveAuctioneersAuction(id=str(auction_id))
        try:
            auction.title = item['title']
        except KeyError:
            pass
        except ValueError:
            print('auction {} received title {} of invalid type {}' \
                .format(auction_id, item['title'], \
                    type(item['title'])))

        try:
            auction.description = self._normalise_text(item_detail['description'])
        except KeyError:
            pass
        except ValueError:
            print('auction {} received description {} of invalid type {}' \
                .format(auction_id, item_detail['description'], \
                    type(item_detail['description'])))

        try:
            isostring = item['publishDate']
            # datetime's ISO 8601 parser doesn't understand 'Z' at the end for UTC,
            # so rewrite it into an easier variant
            if isostring[-1] == 'Z':
                auction.start_time = datetime.fromisoformat(isostring[:-1] + '+00:00')
            else:
                raise KeyError
        except KeyError:
            pass
        except TypeError:
            print('auction {} received start_time {} of invalid type {}' \
                .format(auction_id, isostring, \
                    type(isostring)))

        try:
            auction.end_time = datetime.utcfromtimestamp(catalog['saleStartTs'])
        except KeyError:
            pass
        except TypeError:
            print('auction {} received end_time {} of invalid type {}' \
                .format(auction_id, catalog['saleStartTs'], \
                    type(catalog['saleStartTs'])))

        try:
            auction.n_bids = int(bidding_info['bidCount'])
        except KeyError:
            pass
        except ValueError:
            print('auction {} received n_bids {} of invalid type {}' \
                .format(auction_id, bidding_info['bidCount'], \
                    type(bidding_info['bidCount'])))

        auction.currency = Currency('USD')
        try:
            auction.latest_price = float(bidding_info['salePrice'])
        except KeyError:
            pass
        except ValueError:
            print('auction {} received latest_price {} of invalid type {}' \
                .format(auction_id, bidding_info['salePrice'], \
                    type(bidding_info['salePrice'])))

        try:
            auction.starting_price = float(item['startPrice'])
        except KeyError:
            pass
        except ValueError:
            print('auction {} received starting_price {} of invalid type {}' \
                .format(auction_id, item['startPrice'], \
                    type(item['startPrice'])))

        auction.location = location

        try:
            auction.lot_number = int(re.sub('[^0-9]', '', item['lotNumber']))
        except KeyError:
            pass
        except ValueError:
            print('auction {} received lotNumber {} of invalid type {}' \
                .format(auction_id, item['lotNumber'], \
                    type(item['lotNumber'])))

        auction.image_urls = image_urls
        try:
            auction.condition = item_detail['conditionReport']
        except KeyError:
            pass
        except ValueError:
            print('auction {} received condition {} of invalid type {}' \
                .format(auction_id, item_detail['conditionReport'], \
                    type(item_detail['conditionReport'])))

        try:
            auction.high_bid_estimate = float(item['highBidEstimate'])
        except KeyError:
            pass
        except ValueError:
            print('auction {} received high_bid_estimate {} of invalid type {}' \
                .format(auction_id, item['highBidEstimate'], \
                    type(item['highBidEstimate'])))

        try:
            auction.low_bid_estimate = float(item['lowBidEstimate'])
        except KeyError:
            pass
        except ValueError:
            print('auction {} received low_bid_estimate {} of invalid type {}' \
                .format(auction_id, item['lowBidEstimate'], \
                    type(item['lowBidEstimate'])))

        try:
            auction.seller_id = str(seller_id)
        except KeyError:
            pass

        return auction

    def __parse_auction_page(self, soup, auction_id):
        # Try various parsing methods until one works
        try:
            return self.__parse_2021_auction_soup(soup, auction_id)
        except Exception:
            raise ValueError('Could not parse web page')

    def _scrape_auction_page(self, uri):
        auction_id = urlparse(uri).path.split('/')[2].split('_')[0]
        soup = self._get_page(uri)
        auction = self.__parse_auction_page(soup, auction_id)

        # Add the uri to the auction
        auction.uri = uri
        return auction, soup.prettify()

    ### profile scraping

    def __parse_2021_profile_soup(self, soup, profile_id):

        json = self.__extract_data_json(soup)

        seller = json['seller']['byId'][str(profile_id)]
        seller_detail = json['sellerDetail']['byId'][str(profile_id)]
        seller_ratings = json['sellerRatings']['byId'][str(profile_id)]
        n_followers = json['sellerFollowerCount']['byId'][str(profile_id)]

        # Construct the profile object
        profile = LiveAuctioneersProfile(id=str(profile_id))
        profile.name = seller.get('name')
        profile.description = seller_detail.get('description')
        profile.n_followers = n_followers
        profile.n_ratings = seller_ratings.get('totalReviews')
        profile.rating_out_of_5 = seller_ratings.get('overall')
        profile.location = self.__address_from_seller(seller)

        return profile

    def __parse_profile_page(self, soup, profile_id):
        # Try various parsing methods until one works
        try:
            return self.__parse_2021_profile_soup(soup, profile_id)
        except Exception:
            raise ValueError('Could not parse web page')

    def _scrape_profile_page(self, uri):
        profile_id = urlparse(uri).path.split('/')[2]
        soup = self._get_page(uri)
        profile = self.__parse_profile_page(soup, profile_id)

        # Add the uri to the profile
        profile.uri = uri
        return profile, soup.prettify()

    ### search scraping

    def _scrape_search_page(self, uri):
        soup = self._get_page(uri)
        json = self.__extract_data_json(soup)

        output = {}
        for auction_id in (json['search']['itemIds'] or []):
            item = json['item']['byId'][str(auction_id)]
            output[auction_id] = SearchResult( \
                name=item['title'], uri=self.base_auction_uri.format(auction_id))
            # print(f'Found auction page "{item["title"]}"')

        return output, soup.prettify()

    def _generate_search_uri(self, query_string, n_page):
        if not isinstance(n_page, int) or n_page < 1:
            raise ValueError('n_results must be an int, greater than 0')

        return self.base_search_uri.format(query_string, n_page)
