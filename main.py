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

import typer
from termcolor import colored
import sys
import traceback
import pathlib
import typing
from enum import Enum

from auction_scraper.scrapers.liveauctioneers.scraper import \
    LiveAuctioneersAuctionScraper
from auction_scraper.scrapers.ebay.scraper import \
    EbayAuctionScraper

class Backend(Enum):
    ebay = 'ebay'
    liveauctioneers = 'liveauctioneers'

# Required because apparently Typer doesn't support Enums that map to classes
backend_dict = {
        Backend.ebay : EbayAuctionScraper,
        Backend.liveauctioneers: LiveAuctioneersAuctionScraper
    }

app = typer.Typer()
init_state = {'db_path': None, 'base_uri': None, 'data_location': None, 
        'verbose': None}
state = {}

def setup():
    if state['backend'] == Backend.ebay:
        scraper = EbayAuctionScraper(**init_state)
    elif state['backend'] == Backend.liveauctioneers:
        scraper = LiveAuctioneersAuctionScraper(**init_state)
    else:
        raise ValueError('No valid scraper backend provided')
    return scraper

@app.callback()
def main(db_path: str = typer.Argument(..., help='The path of the sqlite database file to be written to'),
        backend: Backend = typer.Argument(..., help='The auction scraping backend'),
        data_location: str = typer.Option(None, help='The path additional image and html data is saved to'),
        save_images: bool = typer.Option(False, help='Save images to data-location.  Requires --data-location'),
        save_pages: bool = typer.Option(False, help='Save pages to data-location. Requires --data-location'),
        verbose: bool = False,
        base_uri: str = typer.Option(None, help='Override the base url used to resolve the auction site')):
    init_state['db_path'] = db_path
    init_state['data_location'] = data_location
    init_state['verbose'] = verbose
    init_state['base_uri'] = base_uri
    state['save_images'] = save_images
    state['save_pages'] = save_pages
    state['backend'] = backend

@app.command()
def auction(auction: typing.List[str] = typer.Argument(..., help= \
        'A list of auctions to scrape.  Can specify by auction ID or full URI.')):
    """
    Scrapes an auction site auction page.
    """
    scraper = setup()
    for a in auction:
        try:
            scraper.scrape_auction_to_db(a, state['save_pages'], \
                state['save_images'])
        except Exception as e:
            if init_state['verbose']:
                print(colored(traceback.format_exc(), 'red'))
            else:
                print(colored(e, 'red'))

@app.command()
def profile(profile: typing.List[str] = typer.Argument(..., help= \
        'A list of profiles to scrape.  Can specify by profile ID or full URI.')):
    """
    Scrapes an auction site profile page.
    """
    scraper = setup()
    for p in profile:
        try:
            scraper.scrape_profile_to_db(p, state['save_pages'])
        except Exception as e:
            if init_state['verbose']:
                print(colored(traceback.format_exc(), 'red'))
            else:
                print(colored(e, 'red'))

@app.command()
def search(n_results: int = typer.Argument(..., help='The number of results to return'),
        query_string: typing.List[str] = typer.Argument(..., help='A list of query strings to search for')):
    """
    Performs a search, returning the top n_results results for each query_string.
    Scrapes the auction and seller profile for each result.
    """
    scraper = setup()
    try:
        scraper.scrape_search_to_db(query_string, n_results, 
            state['save_pages'], state['save_images'])
    except Exception as e:
        if init_state['verbose']:
            print(colored(traceback.format_exc(), 'red'))
        else:
            print(colored(e, 'red'))

def main():
    app()
