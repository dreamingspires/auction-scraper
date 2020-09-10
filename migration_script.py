import sqlite3
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from auction_scraper.abstract_models import Base
from auction_scraper.scrapers.ebay.models import EbayAuction, EbayProfile
import os
from datetime import datetime

db_path = "mambila_art.db"

def _db_transaction(f):
    for _ in range(5):
        try:
            conn = sqlite3.connect(db_path)
            c = conn.execute('''BEGIN TRANSACTION''')
            try:
                result = f(c)
                conn.commit()
            except:
                conn.rollback()
                raise
            finally:
                try:
                    conn.close()
                except:
                    os.abort()
        except sqlite3.OperationalError as e:
            pass
        else:
            return result
    c = sqlite3.connect(db_path)
    try:
        c.execute('''BEGIN EXCLUSIVE TRANSACTION''')
        result = f(c)
        c.commit()
    finally:
        c.close()

# Create the sqlite database under sqlalchemy
engine = create_engine('sqlite:///' + os.path.abspath('mambila_art_new.db'), echo=True)
Session = sessionmaker(bind=engine)
Base.metadata.create_all(engine)

@_db_transaction
def auctions(c):
    return c.execute("SELECT * FROM ebay_auctions;").fetchall()

@_db_transaction
def profiles(c):
    return c.execute("SELECT * FROM ebay_profiles;").fetchall()


def fill_in_field(table, table_field_name, data, data_field_names, f = lambda x: x):
    try:
        data_field = reduce(lambda x, n: x[n] if x else None, data_field_names, data)
        if data_field:
            setattr(table, table_field_name, f(data_field))
    except KeyError:
        print(f'DEBUG: website data missing field {data_field_names}')
    except ValueError:
        print(f'received {table_field_name} {data_field} of invalid type {type(f(data_field))}')

session = Session()
for a in auctions:
    if a[0] is None:
        raise ValueError('Auction {} has no ID'.format(a[1]))
    auction = EbayAuction(id=str(a[0]))
    auction.title = a[1]
    auction.description = a[13]
    auction.uri = None
    try:
        auction.start_time = datetime.utcfromtimestamp(a[3])
    except TypeError:
        pass
    try:
        auction.end_time = datetime.utcfromtimestamp(a[4])
    except TypeError:
        pass
    auction.n_bids = a[5]
    auction.currency = a[7] # TODO: convert to currency
    auction.latest_price = a[6]
    auction.starting_price = a[9]
    auction.image_urls = None
    auction.image_paths = a[12]
    auction.buy_now_price = a[8]
    auction.location = a[11]
    auction.locale = None
    auction.quantity = None
    auction.video_url = None
    auction.vat_included = None
    auction.domain = None
    if a[2] is not None:
        auction.seller_id = str(a[2])
    if a[10] is not None:
        auction.winner_id = str(a[10])

    session.merge(auction)

for p in profiles:
    if p[0] is None:
        raise ValueError('Auction {} has no ID'.format(p[1]))
    profile = EbayProfile(id=str(p[0]))
    profile.name = p[5]
    profile.description = p[1]
    profile.uri = None
    profile.n_followers = p[10]
    profile.n_reviews = p[11]
    try:
        profile.member_since = datetime.utcfromtimestamp(p[9])
    except TypeError:
        pass
    profile.location = p[4]
    profile.percent_positive_feedback = p[12]
    # contacted email field registered field permission_given never used
    session.merge(profile)

session.commit()
