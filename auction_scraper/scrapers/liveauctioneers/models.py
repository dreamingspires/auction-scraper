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
from sqlalchemy import Table, Column, Integer, String
from sqlalchemy.types import Text
from urllib.parse import urlparse, urljoin

# Define the database models
class LiveAuctioneersProfile(BaseProfile):
    __tablename__ = 'liveauctioneers_profiles'
    n_followers = Column(Integer)
    n_ratings = Column(Integer)
    rating_out_of_5 = Column(Integer)
    location = Column(Text())

class LiveAuctioneersAuction(BaseAuction, metaclass=BaseAuctionRelationshipMeta, \
        profile_table='LiveAuctioneersProfile', \
        profile_table_name='liveauctioneers_profiles'):
    __tablename__ = 'liveauctioneers_auctions'
    location = Column(Text())
    lot_number = Column(Integer)
    condition = Column(Text())
    high_bid_estimate = Column(String(16))
    low_bid_estimate = Column(String(16))
