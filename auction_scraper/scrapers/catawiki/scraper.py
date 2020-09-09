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

import contextlib
from datetime import datetime
from functools import reduce
import json
from os import devnull
from pathlib import Path
import re
import requests
import sys
from urllib.parse import urlparse, urljoin

from auction_scraper.abstract_scraper import AbstractAuctionScraper, \
    SearchResult
from auction_scraper.scrapers.catawiki.models import \
    CataWikiAuction, CataWikiProfile

def fill_in_field(table, table_field_name, data, data_field_names, f = lambda x: x):
    try:
        data_field = reduce(lambda x, n: x[n] if x else None, data_field_names, data)
        if data_field:
            setattr(table, table_field_name, f(data_field))
    except KeyError:
        print(f'DEBUG: website data missing field {data_field_names}')
    except ValueError:
        print(f'received {table_field_name} {data_field} of invalid type {type(f(data_field))}')

class CataWikiAuctionScraper(AbstractAuctionScraper):
    auction_table = CataWikiAuction
    profile_table = CataWikiProfile
    base_uri = 'https://www.catawiki.com'
    auction_suffix = '/l/{}'
    profile_suffix = '/u/{}'
    #search_suffix = '/s/?q={}&page={}&sort=relevancy_desc'
    search_suffix = '/buyer/api/v1/search?q={}&page={}'
    backend_name = 'catawiki'

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

    def __parse_2020_auction_soup(self, soup):
        data = json.loads(soup.find("div", attrs={"class": "lot-details-page-wrapper"})['data-props'])

        auction_id = data['lotId']

        # Construct the auction object
        auction = CataWikiAuction(id=str(auction_id))

        auction.currency = 'EUR' # TODO this could be configured (only this needs changing)

        fill_in_field(auction, 'title', data, ('lotTitle',))
        fill_in_field(auction, 'subtitle', data, ('lotSubtitle',))
        fill_in_field(auction, 'description', data, ('description',), self._normalise_text)
        fill_in_field(auction, 'seller_id', data, ('sellerInfo', 'id'), str)
        fill_in_field(auction, 'lot_details', data, ('specifications',), lambda specs: json.dumps(dict((spec['name'], spec['value']) for spec in specs)))
        fill_in_field(auction, 'image_urls', data, ('images',), lambda imgs: ' '.join((img['large'] for img in imgs)))
        fill_in_field(auction, 'expert_estimate_max', data, ('expertsEstimate', 'max'), json.dumps)
        fill_in_field(auction, 'expert_estimate_min', data, ('expertsEstimate', 'min'), json.dumps)

        bidding_req = requests.get(f'https://www.catawiki.com/buyer/api/v2/lots/{auction_id}/bidding?currency_code={auction.currency}')
        bidding = json.loads(bidding_req.text)

        fill_in_field(auction, 'starting_price', bidding, ('bidding', 'start_bid_amount'))
        fill_in_field(auction, 'latest_price', bidding, ('bidding', 'current_bid_amount'))
        fill_in_field(auction, 'reserve_price_met', bidding, ('bidding', 'reserve_price_met'))
        fill_in_field(auction, 'closed', bidding, ('bidding', 'closed'))
        fill_in_field(auction, 'start_time', bidding, ('bidding', 'bidding_start_time'), lambda t: datetime.fromisoformat(t.rstrip('Z')))
        fill_in_field(auction, 'end_time', bidding, ('bidding', 'bidding_start_time'), lambda t: datetime.fromisoformat(t.rstrip('Z')))
        fill_in_field(auction, 'sold', bidding, ('bidding', 'sold'))

        bids_req = requests.get(f'https://www.catawiki.com/buyer/api/v1/lots/{auction_id}/bids?currency={auction.currency}')
        bids = json.loads(bids_req.text)

        fill_in_field(auction, 'n_bids', bids, ('meta', 'total'))

        return auction

    def __parse_auction_page(self, soup):
        # Try various parsing methods until one works
        try:
            return self.__parse_2020_auction_soup(soup)
        except Exception as e:
            raise ValueError(f'Could not parse web page: {e}')

    def _scrape_auction_page(self, uri):
        soup = self._get_page(uri)
        auction = self.__parse_auction_page(soup)

        # Add the uri to the auction
        auction.uri = uri
        return auction, soup.prettify()

    def __parse_2020_profile_soup(self, soup):
        # Extract profile attributes
        data = json.loads(soup.find("div", attrs={"data-react-component": "LotsFromSellerSidebar"})['data-props'])

        profile_id = data['seller']['id']

        # Construct the profile object
        profile = CataWikiProfile(id=str(profile_id))

        fill_in_field(profile, 'name', data, ('seller', 'userName'))
        fill_in_field(profile, 'member_since', data, ('seller', 'createdAt'), lambda t: datetime.fromisoformat(t.rstrip('Z')))
        fill_in_field(profile, 'feedback_score', data, ('seller', 'score', 'score'))
        fill_in_field(profile, 'positive_reviews', data, ('seller', 'score', 'positiveCount'))
        fill_in_field(profile, 'neutral_reviews', data, ('seller', 'score', 'neutralCount'))
        fill_in_field(profile, 'negative_reviews', data, ('seller', 'score', 'negativeCount'))
        fill_in_field(profile, 'location', data, ('seller', 'address'), json.dumps)

        return profile

    def __parse_profile_page(self, soup):
        # Try various parsing methods until one works
        try:
            return self.__parse_2020_profile_soup(soup)
        except Exception as e:
            raise ValueError(f'Could not parse web page: {e}')

    def _scrape_profile_page(self, uri):
        soup = self._get_page(uri)
        profile = self.__parse_profile_page(soup)

        # Add the uri to the profile
        profile.uri = uri
        return profile, soup.prettify()

    def _scrape_search_page(self, uri):
        soup = self._get_page(uri)

        # NOTE Either of these works, I'm currently calling out to the API as it should be more stable
        # this does mean 2 requests rather than 1 however

        """
        data = json.loads(soup.find("div", attrs={"data-react-component": "SearchResults"})['data-props'])
        output = dict((str(result['id']), SearchResult(result['title'], result['url'])) for result in data['results'])
        """

        search_req = requests.get(uri)
        search = json.loads(search_req.text)
        output = dict((str(result['id']), SearchResult(result['title'], result['url'])) for result in search['lots'])

        return output, soup.prettify()

    def _generate_search_uri(self, query_string, n_page):
        if not isinstance(n_page, int) or n_page < 1:
            raise ValueError('n_results must be an int, greater than 0')
        return self.base_search_uri.format(query_string, n_page)
