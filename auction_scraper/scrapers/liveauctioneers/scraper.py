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
    SearchResult
from auction_scraper.scrapers.liveauctioneers.models import \
    LiveAuctioneersAuction, LiveAuctioneersProfile

class UnexpectedPageError(Exception):
    def __init__(self, page):
        self.message = 'Failed to parse page'
        self.page = page

    def __str__(self):
        return f'{self.message}'

class LiveAuctioneersAuctionScraper(AbstractAuctionScraper):
    auction_table = LiveAuctioneersAuction
    profile_table = LiveAuctioneersProfile
    base_uri = 'https://www.liveauctioneers.com'
    auction_suffix = '/item/{}'
    profile_suffix = '/auctioneer/{}'
    search_suffix = '/search/?keyword={}&page={}'
    backend_name = 'liveauctioneers'


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
        address_strings = [seller['address'], seller['address2'], seller['city'], seller['country']]
        location = '\n'.join(filter(None, address_strings))
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

    def __rating_calc(self, soup):
        ratings = soup.find('div', attrs={'class' : re.compile('ratingsCounter')})
        numerics = ratings.find_all('span', attrs={'class' : re.compile('blue')})
        num2 = []
        for i in numerics:
            rawdata = i.text
            raw_number = rawdata[rawdata.find('(')+1:rawdata.find(')')]
            ratingout = int(re.sub('[^0-9]', '', raw_number))
            num2.append(ratingout)
        sum_rating = 0
        total_ratings = 0

        for i in range(1,6):
            sum_rating += num2[-i]*(i)
            total_ratings += num2[-i]
        mean_rating = round(sum_rating/total_ratings,1)
        return mean_rating, total_ratings

    def __parse_2020_profile_soup(self, soup, profile_id):
        # Extract profile attributes
        try:
            description = soup.find('div', attrs= \
                {'class' : re.compile('seller-about-text')}).text
        except AttributeError:
            description = None
        n_followers = soup.find('div', attrs= \
            {'class' : re.compile('followers')}).text.split(' ')[0] \
            .replace(',','')
        try:
            address = soup.find('div', attrs= \
                {'class' : re.compile('Address__StyledAddress')}).text
        except AttributeError:
            address = None
        rating, n_ratings = self.__rating_calc(soup)
        try:
            auctioneer_name = soup.find('span', attrs= \
                {'class' : re.compile('titleName')}).text
        except AttributeError:
            auctioneer_name = None

        # Construct the profile object
        profile = LiveAuctioneersProfile(id=str(profile_id))
        profile.name = auctioneer_name
        profile.description = description

        try:
            profile.n_followers = int(re.sub('[^0-9]', '', n_followers))
        except ValueError:
            print('profile {} received n_followers {} of invalid type {}' \
                .format(profile_id, n_followers, type(n_followers)))

        try:
            profile.n_ratings = n_ratings
        except ValueError:
            print('profile {} received n_ratings {} of invalid type {}' \
                .format(profile_id, n_ratings, type(n_ratings)))

        try:
            profile.rating_out_of_5 = rating
        except ValueError:
            print('profile {} received rating_out_of_5 {} of invalid type {}' \
                .format(profile_id, rating, type(rating)))

        profile.location = address

        return profile

    def __parse_profile_page(self, soup, profile_id):
        # Try various parsing methods until one works
        try:
            return self.__parse_2020_profile_soup(soup, profile_id)
        except Exception:
            raise ValueError('Could not parse web page')

    def _scrape_profile_page(self, uri):
        profile_id = urlparse(uri).path.split('/')[2]
        soup = self._get_page(uri)
        profile = self.__parse_profile_page(soup, profile_id)

        # Add the uri to the profile
        profile.uri = uri
        return profile, soup.prettify()

    def _scrape_search_page(self, uri):
        soup = self._get_page(uri)
        json = self.__extract_data_json(soup)

        output = {}
        for auction_id in (json['search']['itemIds'] or []):
            item = json['item']['byId'][str(auction_id)]
            output[auction_id] = SearchResult( \
                name=item['title'], uri=self.base_auction_uri.format(auction_id))
            print(item['title'])

        return output, soup.prettify()

    def _generate_search_uri(self, query_string, n_page):
        if not isinstance(n_page, int) or n_page < 1:
            raise ValueError('n_results must be an int, greater than 0')

        return self.base_search_uri.format(query_string, n_page)
