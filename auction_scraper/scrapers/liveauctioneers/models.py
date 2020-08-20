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
    image_urls = Column(Text())
    condition = Column(Text())
    high_bid_estimate = Column(String(16))
    low_bid_estimate = Column(String(16))
