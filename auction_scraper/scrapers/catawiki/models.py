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

from auction_scraper.abstract_models import BaseAuction, BaseProfile, \
    BaseAuctionRelationshipMeta
from auction_scraper.abstract_scraper import AbstractAuctionScraper
from sqlalchemy import Table, Column, Boolean, DateTime, Float, Integer, String
from sqlalchemy.types import Text

# Define the database models
class CataWikiProfile(BaseProfile):
    __tablename__ = 'catawiki_profiles'
    member_since = Column(DateTime)
    feedback_score = Column(Float)
    positive_reviews = Column(Integer)
    neutral_reviews = Column(Integer)
    negative_reviews = Column(Integer)
    location = Column(Text())

class CataWikiAuction(BaseAuction, metaclass=BaseAuctionRelationshipMeta, \
        profile_table='CataWikiProfile', \
        profile_table_name='catawiki_profiles'):
    __tablename__ = 'catawiki_auctions'
    subtitle = Column(Text())
    lot_details = Column(Text())
    expert_estimate_max = Column(String(16))
    expert_estimate_min = Column(String(16))
    reserve_price_met = Column(Boolean)
    closed = Column(Boolean)
    sold = Column(Boolean)
