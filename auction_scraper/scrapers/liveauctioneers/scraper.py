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
import contextlib

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

class LiveAuctioneersAuctionScraper(AbstractAuctionScraper):
    auction_table = LiveAuctioneersAuction
    profile_table = LiveAuctioneersProfile
    base_uri = 'https://www.liveauctioneers.com'
    auction_suffix = '/item/{}'
    profile_suffix = '/auctioneer/{}'
    search_suffix = '/search/?keyword={}&page={}'
    backend_name = 'liveauctioneers'

    def __quote_cleaner(self, phrase):
        try:
            return int(phrase)
        except ValueError:
            if isinstance(phrase, str):
                if phrase == 'true':
                    return True
                elif phrase == 'false':
                    return False
                elif phrase == 'null':
                    return None
                else:
                    if phrase[0]=='"' and phrase[-1]=='"':
                        return phrase[1:-1]
                    else:
                        return phrase
            else:
                raise ValueError('Unknown type')

    def __searcher(self, soup, phrase, phrase2):
        soupstr = str(soup)
        start_index = soupstr.find(phrase)
        end_index = soupstr.find(phrase2, start_index+len(phrase))
        output = soupstr[start_index+len(phrase):end_index]
        return output

    def __parse_2020_auction_soup(self, soup, auction_id):
        def get_embedded_json():
            jsonsect = self.__searcher(soup, \
                '<script data-reactroot="">', '</script>')

            # Bodge until we get rid of slimit
            with silence_output():
                parser = Parser()
            
            tree = parser.parse(jsonsect)
            j=0
            element = {}
            for node in nodevisitor.visit(tree):
                try:
                    if isinstance(node, ast.Assign):
                        if node.left.value=='"'+str(auction_id)+'"':
                            for i in node.right.children():
                                leftp = i.left.value[1:-1]
                                rightp = i.right.value
                                element[leftp] = self.__quote_cleaner(rightp)
                except:
                    pass

            return element

        def get_embedded_image_urls():
            divs = soup.find_all('div', attrs= \
                {'class' : re.compile('thumbnail-container')})
            urls = []
            for i in divs:
                urllist = i.find('img')['src'].split('.')
                urllist[-1] = 'jpg'
                urls.append('.'.join(urllist))
            return urls

        
        element = get_embedded_json()

        # Extract additional auctioneer info
        div = soup.find('div', attrs={'class' : re.compile('auctioneerInfo')})
        # TODO: try/except on these
        auctioneer = self.__quote_cleaner( \
            div.find('h3', attrs= {'class' : re.compile('sellerName')}).text)
        location = self.__quote_cleaner( \
            div.find('div', attrs={'class' : re.compile('address')}).text)
        image_urls = ' '.join(get_embedded_image_urls())

        # Construct the auction object
        auction = LiveAuctioneersAuction(id=str(auction_id))
        try:
            auction.title = element['title']
        except KeyError:
            pass
        except ValueError:
            print('auction {} received title {} of invalid type {}' \
                .format(auction_id, element['title'], \
                    type(element['title'])))

        try:
            auction.description = self._normalise_text(element['description'])
        except KeyError:
            pass
        except ValueError:
            print('auction {} received description {} of invalid type {}' \
                .format(auction_id, element['description'], \
                    type(element['description'])))

        try:
            auction.start_time = datetime.utcfromtimestamp( \
                element['availableTs'])
        except KeyError:
            pass
        except TypeError:
            print('auction {} received start_time {} of invalid type {}' \
                .format(auction_id, element['availableTs'], \
                    type(element['availableTs'])))

        try:
            auction.end_time = datetime.utcfromtimestamp( \
                element['saleStartTs'])
        except KeyError:
            pass
        except TypeError:
            print('auction {} received end_time {} of invalid type {}' \
                .format(auction_id, element['saleStartTs'], \
                    type(element['saleStartTs'])))

        try:
            auction.n_bids = int(element['bidCount'])
        except KeyError:
            pass
        except ValueError:
            print('auction {} received n_bids {} of invalid type {}' \
                .format(auction_id, element['bidCount'], \
                    type(element['bidCount'])))

        # TODO: ensure the currency is of the correct format
        auction.currency = 'USD'
        try:
            auction.latest_price = float(element['salePrice'])
        except KeyError:
            pass
        except ValueError:
            print('auction {} received latest_price {} of invalid type {}' \
                .format(auction_id, element['salePrice'], \
                    type(element['salePrice'])))

        try:
            auction.starting_price = float(element['startPrice'])
        except KeyError:
            pass
        except ValueError:
            print('auction {} received starting_price {} of invalid type {}' \
                .format(auction_id, element['startPrice'], \
                    type(element['startPrice'])))

        auction.location = location

        try:
            auction.lot_number = int(element['lotNumber'])
        except KeyError:
            pass
        except ValueError:
            print('auction {} received lotNumber {} of invalid type {}' \
                .format(auction_id, element['lotNumber'], \
                    type(element['lotNumber'])))

        auction.image_urls = image_urls
        try:
            auction.condition = str(element['conditionReport'])
        except KeyError:
            pass
        except ValueError:
            print('auction {} received condition {} of invalid type {}' \
                .format(auction_id, element['conditionReport'], \
                    type(element['conditionReport'])))

        try:
            auction.high_bid_estimate = float(element['highBidEstimate'])
        except KeyError:
            pass
        except ValueError:
            print('auction {} received high_bid_estimate {} of invalid type {}' \
                .format(auction_id, element['highBidEstimate'], \
                    type(element['highBidEstimate'])))

        try:
            auction.low_bid_estimate = float(element['lowBidEstimate'])
        except KeyError:
            pass
        except ValueError:
            print('auction {} received low_bid_estimate {} of invalid type {}' \
                .format(auction_id, element['lowBidEstimate'], \
                    type(element['lowBidEstimate'])))

        try:
            auction.seller_id = str(element['sellerId'])
        except KeyError:
            pass

        return auction

    def __parse_auction_page(self, soup, auction_id):
        # Try various parsing methods until one works
        try:
            return self.__parse_2020_auction_soup(soup, auction_id)
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
            ratingout = int(rawdata[rawdata.find('(')+1:rawdata.find(')')])
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
        description = soup.find('div', attrs= \
            {'class' : re.compile('seller-about-text')}).text
        n_followers = soup.find('div', attrs= \
            {'class' : re.compile('followers')}).text.split(' ')[0] \
            .replace(',','')
        address = soup.find('div', attrs= \
            {'class' : re.compile('Address__StyledAddress')}).text
        rating, n_ratings = self.__rating_calc(soup)
        auctioneer_name = soup.find('span', attrs= \
            {'class' : re.compile('titleName')}).text

        # Construct the profile object
        profile = LiveAuctioneersProfile(id=str(profile_id))
        profile.name = auctioneer_name
        profile.description = description

        try:
            profile.n_followers = int(n_followers)
        except ValueError:
            print('profile {} received n_followers {} of invalid type {}' \
                .format(profile_id, n_followers, type(n_followers)))

        try:
            profile.n_ratings = int(n_ratings)
        except ValueError:
            print('profile {} received n_ratings {} of invalid type {}' \
                .format(profile_id, n_ratings, type(n_ratings)))

        try:
            profile.rating_out_of_5 = int(rating)
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
        results_grid = soup.find_all('div', attrs={'class' : re.compile('item-title-container')})

        output={}
        for result in results_grid:
            link = result.find('a')
            if link is None:
                continue
            href = link['href']
            auction_id = href.split('/')[2].split('_')[0]
            output[auction_id] = SearchResult( \
                name=result.text, uri=self.base_auction_uri.format(auction_id))

        return output, soup.prettify()

    def _generate_search_uri(self, query_string, n_page):
        if not isinstance(n_page, int) or n_page < 1:
            raise ValueError('n_results must be an int, greater than 0')
    
        return self.base_search_uri.format(query_string, n_page)
