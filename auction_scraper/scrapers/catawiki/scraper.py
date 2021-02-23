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
"""
A scraper for catawiki.com
"""

from datetime import datetime
import json
from urllib.parse import urljoin

from auction_scraper.abstract_scraper import AbstractAuctionScraper, \
    SearchResult
from auction_scraper.scrapers.catawiki.models import \
    CataWikiAuction, CataWikiProfile


def fill_in_field(table, table_field_name,
                  data, data_field_names,
                  default,
                  process=lambda x: x):
    """
    Extract from data the field at path data_field_names \
    and assign it to property table_field_name of table
    Optionally processing it with a function
    """
    try:
        data_field = data
        for field_name in data_field_names:
            if not data_field:
                break
            data_field = data_field[field_name]
        if data_field:
            setattr(table, table_field_name, process(data_field))
        else:
            setattr(table, table_field_name, default)
    except KeyError:
        print(f'DEBUG: website data missing field {data_field_names}')
    except ValueError:
        print(f'received {table_field_name} {data_field} \
             of invalid type {type(process(data_field))}')


def json_dumps_unicode(data):
    return json.dumps(data, ensure_ascii=False)


class CataWikiAuctionScraper(AbstractAuctionScraper):
    """
    A scraper for catawiki.com
    """
    auction_table = CataWikiAuction
    profile_table = CataWikiProfile
    base_uri = 'https://www.catawiki.com'
    auction_suffix = '/l/{}'
    profile_suffix = '/u/{}'
    search_suffix = '/buyer/api/v1/search?q={}&page={}'
    backend_name = 'catawiki'

    currency = 'EUR'

    bidding_api_uri_suffix = \
            f'/buyer/api/v2/lots/{{}}/bidding?currency_code={currency}'
    bids_api_uri_suffix = \
            f'/buyer/api/v1/lots/{{}}/bids?currency={currency}'

    base_bidding_api_uri = urljoin(base_uri, bidding_api_uri_suffix)
    base_bids_api_uri = urljoin(base_uri, bids_api_uri_suffix)

    def __parse_2020_auction_soup(self, soup):
        json_div_attrs = {"class": "lot-details-page-wrapper"}
        data_json = soup.find("div", attrs=json_div_attrs)['data-props']
        meta = json.loads(data_json)

        # Construct the auction object
        auction_id = meta['lotId']
        auction = CataWikiAuction(id=str(auction_id))
        auction.currency = self.currency

        # TODO: create bidding class to store bidding info

        auction = self.__parse_auction_meta(auction, meta)
        auction = self.__parse_auction_category(auction, soup)
        auction = self.__parse_bidding(auction)
        auction = self.__parse_bids(auction)

        return auction

    def __parse_auction_meta(self, auction, meta):

        def extract_lot_details(specs):
            details = dict((spec['name'], spec['value']) for spec in specs)
            return json_dumps_unicode(details)

        def combine_image_urls(imgs):
            return ' '.join((img['large'] for img in imgs))

        fill_in_field(auction, 'title',
                      meta, ('lotTitle',),
                      default="")
        fill_in_field(auction, 'subtitle',
                      meta, ('lotSubtitle',),
                      default="")
        fill_in_field(auction, 'description',
                      meta, ('description',),
                      default="",
                      process=self._normalise_text)
        fill_in_field(auction, 'seller_id',
                      meta, ('sellerInfo', 'id'),
                      default="",
                      process=str)
        fill_in_field(auction, 'lot_details',
                      meta, ('specifications',),
                      default="{}",
                      process=extract_lot_details)
        fill_in_field(auction, 'image_urls',
                      meta, ('images',),
                      default="",
                      process=combine_image_urls)
        fill_in_field(auction, 'expert_estimate_max',
                      meta, ('expertsEstimate', 'max', self.currency),
                      default=-1)
        fill_in_field(auction, 'expert_estimate_min',
                      meta, ('expertsEstimate', 'min', self.currency),
                      default=-1)

        return auction

    def __parse_bidding(self, auction):
        bidding = self._get_json(self.base_bidding_api_uri.format(auction.id))

        fill_in_field(auction, 'starting_price',
                      bidding, ('bidding', 'start_bid_amount'),
                      default=-1)
        fill_in_field(auction, 'latest_price',
                      bidding, ('bidding', 'current_bid_amount'),
                      default=-1)
        fill_in_field(auction, 'reserve_price_met',
                      bidding, ('bidding', 'reserve_price_met'),
                      default=False)
        fill_in_field(auction, 'closed',
                      bidding, ('bidding', 'closed'),
                      default=False)
        fill_in_field(auction, 'start_time',
                      bidding, ('bidding', 'bidding_start_time'),
                      default=None,
                      process=lambda t: datetime.fromisoformat(t.rstrip('Z')))
        fill_in_field(auction, 'end_time',
                      bidding, ('bidding', 'bidding_start_time'),
                      default=None,
                      process=lambda t: datetime.fromisoformat(t.rstrip('Z')))
        fill_in_field(auction, 'sold',
                      bidding, ('bidding', 'sold'),
                      default=False)

        return auction

    def __parse_bids(self, auction):
        bids = self._get_json(self.base_bids_api_uri.format(auction.id))

        fill_in_field(auction, 'n_bids',
                      bids, ('meta', 'total'),
                      default=-1)

        return auction

    def __parse_auction_category(self, auction, soup):
        json_script_attrs = {"type": "application/ld+json"}
        breadcrumb_list_json = soup.find("script", attrs=json_script_attrs).string
        breadcrumb_list = json.loads(breadcrumb_list_json)

        def extract_categories(cats):
            categories = dict((cat['item']['name'], cat['item']['@id']) for cat in cats \
                        if cat['item']['name'] != 'Catawiki')
            return json_dumps_unicode(categories)

        fill_in_field(auction, 'categories',
                      breadcrumb_list, ('itemListElement', ),
                      default="{}",
                      process=extract_categories)

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
        json_div_attrs = {"data-react-component": "LotsFromSellerSidebar"}
        data_json = soup.find("div", attrs=json_div_attrs)['data-props']
        data = json.loads(data_json)

        profile_id = data['seller']['id']

        # Construct the profile object
        profile = CataWikiProfile(id=str(profile_id))

        fill_in_field(profile, 'name',
                      data, ('seller', 'sellerName'),
                      default="")
        fill_in_field(profile, 'member_since',
                      data, ('seller', 'createdAt'),
                      default=None,
                      process=lambda t: datetime.fromisoformat(t.rstrip('Z')))
        fill_in_field(profile, 'feedback_score',
                      data, ('seller', 'score', 'score'),
                      default=-1)
        fill_in_field(profile, 'positive_reviews',
                      data, ('seller', 'score', 'positiveCount'),
                      default=-1)
        fill_in_field(profile, 'neutral_reviews',
                      data, ('seller', 'score', 'neutralCount'),
                      default=-1)
        fill_in_field(profile, 'negative_reviews',
                      data, ('seller', 'score', 'negativeCount'),
                      default=-1)
        fill_in_field(profile, 'location',
                      data, ('seller', 'address'),
                      default="{}",
                      process=json_dumps_unicode)

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
        data = self._get_json(uri)

        output = {}
        for result in data['lots']:
            output[str(result['id'])] = \
                    SearchResult(result['title'], result['url'])

        return output, json_dumps_unicode(data)

    def _generate_search_uri(self, query_string, n_page):
        if not isinstance(n_page, int) or n_page < 1:
            raise ValueError('n_results must be an int, greater than 0')
        return self.base_search_uri.format(query_string, n_page)
