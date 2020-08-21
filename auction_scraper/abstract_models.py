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

from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Table, Column, Integer, String, DateTime
from sqlalchemy.types import Text
from sqlalchemy.schema import ForeignKey
from sqlalchemy_utils import CurrencyType
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative.api import DeclarativeMeta
from datetime import datetime

Base = declarative_base()

#class BaseAuctionImage(Base, Image):
#    auction_id = Column(Text(), ForeignKey('base_auction.id'), primary_key=True)

class TimestampBase(Base):
    __abstract__ = True

    date_created = Column(DateTime,  default=datetime.utcnow, nullable=False)
    date_modified = Column(DateTime,  default=datetime.utcnow, nullable=False)

class BaseAuctionRelationshipMeta(DeclarativeMeta):
    def __new__(cls, clsname, bases, namespace, profile_table=None,
            profile_table_name=None):
        namespace['seller_id'] = Column(Text(), 
            ForeignKey(profile_table_name + '.id'))
        namespace['winner_id'] = Column(Text(), 
            ForeignKey(profile_table_name + '.id'))
        namespace['seller'] = relationship(profile_table, \
            backref='auctions_sold', foreign_keys=clsname + '.seller_id')
        namespace['winner'] = relationship(profile_table, \
            backref='auctions_won', foreign_keys=clsname + '.winner_id')
        return super(BaseAuctionRelationshipMeta, cls). \
            __new__( cls, clsname, bases, namespace)
    
    # Must be defined since DeclarativeBase apparently (unlike type)
    # can't "handle extra keyword arguments gracefully"
    # https://stackoverflow.com/questions/13762231/how-to-pass-arguments-to-the-metaclass-from-the-class-definition
    def __init__(cls, clsname, bases, namespace, profile_table=None, 
            profile_table_name=None, **kwargs):
        super(BaseAuctionRelationshipMeta, cls). \
            __init__(clsname, bases, namespace, **kwargs)

class BaseAuction(TimestampBase):
    __abstract__ = True
    __tablename__ = 'base_auction'

    id = Column(Text(), primary_key=True)
    title = Column(Text())
    description = Column(Text())
    uri = Column(Text())
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    n_bids = Column(Integer)
    currency = Column(CurrencyType)
    latest_price = Column(String(16))
    starting_price = Column(String(16))
    # image_urls: space-separated urls
    image_urls = Column(Text())
    # image_paths: colon-separated paths relative to some base path
    image_paths = Column(Text(), nullable=False, default='')

class BaseProfile(TimestampBase):
    __abstract__ = True
    __tablename__ = 'base_profile'

    id = Column(Text(), primary_key=True)
    name = Column(Text())
    description = Column(Text())
    uri = Column(Text())
