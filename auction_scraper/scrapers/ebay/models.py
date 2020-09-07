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

from auction_scraper.abstract_scraper import AbstractAuctionScraper
from auction_scraper.abstract_models import BaseAuction, BaseProfile, \
    BaseAuctionRelationshipMeta
from sqlalchemy import Table, Column, Integer, String, DateTime, Boolean
from sqlalchemy.types import Text
from urllib.parse import urlparse, urljoin

# Define the database models
class EbayProfile(BaseProfile):
    __tablename__ = 'ebay_profiles'
    n_followers = Column(Integer)
    n_reviews = Column(Integer)
    member_since = Column(DateTime)
    location = Column(Text())
    percent_positive_feedback = Column(Integer)

class EbayAuction(BaseAuction, metaclass=BaseAuctionRelationshipMeta, \
        profile_table='EbayProfile', profile_table_name='ebay_profiles'):
    __tablename__ = 'ebay_auctions'
    buy_now_price = Column(String(16))
    location = Column(Text())
    locale = Column(Text())
    quantity = Column(Integer())
    video_url = Column(Text())
    vat_included = Column(Boolean)
    domain = Column(Text())
