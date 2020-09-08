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
from datetime import datetime
from sqlalchemy_utils import Currency
import json
import unicodedata
import dateutil.parser
import re
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
from auction_scraper.scrapers.ebay.models import \
    EbayAuction, EbayProfile

class EbayAuctionScraper(AbstractAuctionScraper):
    auction_table = EbayAuction
    profile_table = EbayProfile
    base_uri = 'https://www.ebay.com'
    auction_suffix = '/itm/{}'
    profile_suffix = '/usr/{}'
    search_suffix = '/sch/i.html?_nkw={}&_pgn={}&_skc=0' # If _skc=0 not specified, the resulting
        # search list is significantly harder to scraper, not containing
        # 'ListViewInner', or even auction IDs within the div
    backend_name = 'ebay'

    # the raw values that appear multiple times in the API
    auction_duplicates = ['maxImageUrl', 'displayImgUrl']

    def __get_dict_value(self, d, k):
        try:
            d[k]
        except KeyError:
            return None

        if d[k] == 'true':
            return True
        elif d[k] == 'false':
            return False
        elif d[k] == 'null':
            return None

        try:
            return int(d[k])
        except Exception:
            pass

        try:
            return float(d[k])
        except Exception:
            pass

        return d[k]

    def __parse_ancient_auction_soup(self, soup, duplicates):
        raise NotImplementedError

    def __parse_2010_auction_soup(self, soup, duplicates):
        raise NotImplementedError

    def __parse_2020_auction_soup(self, soup, duplicates):
        def get_embedded_json():
            # Strip c from s, without exception
            def strip(s, c):
                if isinstance(s, str):
                    return s.strip(c)
                return s

            div = soup.find('div', id='JSDF')
            scripts = div.find_all('script', src=None)

            # Look for $rwidgets
            script_texts = []
            for script in scripts:
                for s in script.contents:
                    if '$rwidgets' in s:
                        script_texts.append(s)

            # Bodge until we get rid of slimit
            with silence_output():
                parser = Parser()

            raw_values = {}
            for script_text in script_texts:
                tree = parser.parse(script_text)
                # Parsing js
                for node in nodevisitor.visit(tree):
                    if isinstance(node, ast.FunctionCall):
                        if isinstance(node.identifier, ast.Identifier):
                            if node.identifier.value == '$rwidgets':
                                # Deal with here
                                fields = {}
                                for n in nodevisitor.visit(node):
                                    if isinstance(n, ast.Assign):
                                        k = getattr(n.left, 'value', '').strip('"')
                                        v = strip(getattr(n.right, 'value', ''), '"')
                                        if k in duplicates:
                                            try:
                                                fields[k].append(v)
                                            except KeyError:
                                                fields[k] = [v]
                                        else:
                                            fields[k] = v

                                # Merge fields and raw_values, resolving duplicates
                                for (k, v) in fields.items():
                                    if k in duplicates:
                                        try:
                                            raw_values[k] += v
                                        except KeyError:
                                            raw_values[k] = v
                                    elif v != 'null':
                                        raw_values[k] = v
            return raw_values
        
        def get_image_urls(raw_values):
            # TODO: sometimes only displayImgUrl is given, when the s-l600 image exists
            # Example: https://www.ebay.com/itm/Chubby-Blob-Seal-Plush-Toy-Animal-Cute-Ocean-Pillow-Pet-Stuffed-Doll-Kids-Gift/362995774962
            # Example: https://i.ebayimg.com/images/g/6NkAAOSwkEFd50Kb/s-l600.jpg
            raw_image_urls = []
            if 'maxImageUrl' in raw_values.keys():
                if 'displayImgUrl' in raw_values.keys():
                    for max_image, disp_image in zip( \
                            self.__get_dict_value(raw_values, 'maxImageUrl'), \
                            self.__get_dict_value(raw_values, 'displayImgUrl')):
                        if max_image == 'null':
                            if disp_image != 'null':
                                raw_image_urls.append(disp_image)
                        else:
                            raw_image_urls.append(max_image)
                else:
                    raw_image_urls = self.__get_dict_value(raw_values, 'maxImageUrl')
            else:
                if 'displayImgUrl' in raw_values.keys():
                    raw_image_urls = self.__get_dict_value(raw_values, 'displayImgUrl')

            def f(url):
                return json.loads('"{}"'.format(url))
            return list(map(f, raw_image_urls))

        # Get raw values, validating API assumptions
        raw_values = get_embedded_json()
        #pdb.set_trace()
        # TODO: some auctions don't include the title in their raw values
        # e.g. https://www.ebay.co.uk/itm/Pair-of-Mambila-Statues-Cameroon/333648332890
        try:
            raw_values['it']
        except KeyError:
            try:
                raw_values['kw']
            except KeyError:
                # No title field available in API.  Obtain it from the page instead
                for c in soup.find('h1', id='itemTitle').contents:
                    if isinstance(c, str):
                        title_key = c
                        break
            else:
                title_key = 'kw'
        else:
            title_key = 'it'

        try:
            if raw_values['entityId'] != raw_values['entityName']:
                print(f'notify author: entityid==entityname assumption incorrect for domain {url}')
        except KeyError:
            print('notify author: entityid or entityname does not exist for auction {}, for domain {}.'.format(raw_values['itemId'], url))
        
        # Extract additional info
        image_urls = get_image_urls(raw_values)
        iframe = soup.find('div', attrs={'id': 'desc_div'}).find('iframe')
        desc = iframe.text

        # Construct the auction object
        # TODO: verbose mode to print which keys were missing (KeyErrors)
        auction = EbayAuction(id=int(raw_values['itemId']))

        try:
            auction.title = unicodedata.normalize("NFKD", raw_values[title_key])
        except (KeyError, TypeError):
            pass
        except ValueError:
            print('auction {} received title {} of invalid type {}' \
                .format(auction_id, raw_values[title_key], \
                    type(raw_values[title_key])))

        try:
            auction.description = self._normalise_text(desc)
        except ValueError:
            print('auction {} received description {} of invalid type {}' \
                .format(auction_id, desc, \
                    type(desc)))

        try:
            auction.seller_id = str(raw_values['entityName'])
        except (KeyError, TypeError):
            pass
        except ValueError:
            print('auction {} received seller_id {} of invalid type {}' \
                .format(auction_id, raw_values['entityName'], \
                    type(raw_values['entityName'])))

        try:
            auction.start_time = datetime.utcfromtimestamp( \
                int(raw_values['startTime'])/1000)
        except (KeyError, TypeError):
            pass
        except ValueError:
            print('auction {} received start_time {} of invalid type {}' \
                .format(auction_id, raw_values['startTime'], \
                    type(raw_values['startTime'])))

        try:
            auction.end_time = datetime.utcfromtimestamp( \
                int(raw_values['endTime'])/1000)
        except (KeyError, TypeError):
            pass
        except ValueError:
            print('auction {} received end_time {} of invalid type {}' \
                .format(auction_id, raw_values['endTime'], \
                    type(raw_values['endTime'])))

        try:
            auction.n_bids = int(raw_values['bids'])
        except (KeyError, TypeError):
            pass
        except ValueError:
            print('auction {} received n_bids {} of invalid type {}' \
                .format(auction_id, raw_values['bids'], \
                    type(raw_values['bids'])))

        try:
            auction.currency = Currency(raw_values['ccode'])
        except (KeyError, TypeError):
            pass
        except ValueError:
            print('auction {} received currency {} of invalid type {}' \
                .format(auction_id, raw_values['ccode'], \
                    type(raw_values['ccode'])))

        try:
            auction.latest_price = str(float(raw_values['bidPriceDouble']))
        except (KeyError, TypeError):
            pass
        except ValueError:
            print('auction {} received latest_price {} of invalid type {}' \
                .format(auction_id, raw_values['bidPriceDouble'], \
                    type(raw_values['bidPriceDouble'])))

        try:
            auction.buy_now_price = str(float(raw_values['binPriceDouble']))
        except (KeyError, TypeError):
            pass
        except ValueError:
            print('auction {} received buy_now_price {} of invalid type {}' \
                .format(auction_id, raw_values['binPriceDouble'], \
                    type(raw_values['binPriceDouble'])))

        # TODO: add starting price, winner, location
        auction.image_urls = ' '.join(image_urls)

        try:
            auction.locale = str(raw_values['locale'])
        except (KeyError, TypeError):
            pass
        except ValueError:
            print('auction {} received locale {} of invalid type {}' \
                .format(auction_id, raw_values['locale'], \
                    type(raw_values['locale'])))

        try:
            auction.quantity = int(raw_values['totalQty'])
        except (KeyError, TypeError):
            pass
        except ValueError:
            print('auction {} received quantity {} of invalid type {}' \
                .format(auction_id, raw_values['quantity'], \
                    type(raw_values['quantity'])))

        try:
            auction.video_url = str(raw_values['videoUrl'])
        except (KeyError, TypeError):
            pass
        except ValueError:
            print('auction {} received video_url {} of invalid type {}' \
                .format(auction_id, raw_values['videoUrl'], \
                    type(raw_values['videoUrl'])))

        try:
            if str(raw_values['vatIncluded']) == 'true':
                auction.vat_included = True
            elif str(raw_values['vatIncluded']) == 'false':
                auction.vat_included = False
            else:
                print('auction {} received vat_included {} of invalid type {}' \
                    .format(auction_id, raw_values['vatIncluded'], \
                        type(raw_values['vatIncluded'])))
        except (KeyError, TypeError):
            pass

        try:
            auction.domain = raw_values['currentDomain']
        except (KeyError, TypeError):
            pass
        except ValueError:
            print('auction {} received domain {} of invalid type {}' \
                .format(auction_id, raw_values['currentDomain'], \
                    type(raw_values['currentDomain'])))

        return auction

    def __parse_auction_page(self, soup):
        # Try various parsing methods until one works
        try:
            return self.__parse_2020_auction_soup(soup, self.auction_duplicates)
        except Exception:
            raise ValueError('Could not parse web page')

    def _scrape_auction_page(self, uri):
        soup = self._get_page(uri)
        auction = self.__parse_auction_page(soup)

        # Add the uri to the auction
        auction.uri = uri
        return auction, soup.prettify()

    def __parse_2020_profile_soup(self, soup, profile_id):
        # Extract profile attributes
        description = soup.find('h2', attrs={'class': 'bio inline_value'}).get_text(strip=True)
        member_info = soup.find('div', id='member_info')
        #n_followers = member_info.find('span', text='Followers').find('span',
        #        attrs={'class': 'info'}).text
        n_followers = None  # Appears obfuscated
        n_reviews = None    # Appears obfuscated
        member_since = dateutil.parser.parse( \
            member_info.find('span', text=re.compile('.*Member since:.*')) \
                .parent.find('span', attrs={'class': 'info'}).get_text(strip=True))
        location = member_info.find('span', attrs={'class': 'mem_loc'}).get_text(strip=True)
        percent_positive_feedback = soup.find('div', attrs={'class': 'perctg'}) \
                .get_text(strip=True).split('%')[0]

        # Construct the profile object
        profile = EbayProfile(id=str(profile_id))
        profile.name = str(profile_id)
        profile.description = description

        try:
            profile.n_followers = int(n_followers)
        except TypeError:
            pass
        except ValueError:
            print('profile {} received n_followers {} of invalid type {}' \
                .format(profile_id, n_followers, \
                    type(n_followers)))
        try:
            profile.n_reviews = int(n_reviews)
        except TypeError:
            pass
        except ValueError:
            print('profile {} received n_reviews {} of invalid type {}' \
                .format(profile_id, n_reviews, \
                    type(n_reviews)))

        profile.member_since = member_since

        try:
            profile.location = str(location)
        except ValueError:
            print('profile {} received location {} of invalid type {}' \
                .format(profile_id, location, \
                    type(location)))

        try:
            profile.percent_positive_feedback = int(percent_positive_feedback)
        except TypeError:
            pass
        except ValueError:
            print('profile {} received percent_positive_feedback {} of invalid type {}' \
                .format(profile_id, percent_positive_feedback, \
                    type(percent_positive_feedback)))

        return profile

    def __parse_profile_page(self, soup, profile_id):
        # Try various parsing methods until one works
        try:
            return self.__parse_2020_profile_soup(soup, profile_id)
        except Exception:
            raise ValueError('Could not parse web page')

    def _scrape_profile_page(self, uri):
        profile_id = urlparse(uri).path.split('/')[-1]  # TODO: is this correct?
        soup = self._get_page(uri)
        profile = self.__parse_profile_page(soup, profile_id)

        # Add the uri to the profile
        profile.uri = uri
        return profile, soup.prettify()

    def __parse_2020_search_soup(self, soup):
        auctions_list = soup.find('ul', id='ListViewInner')
        if auctions_list is None:
            auctions_list = soup.find('ul', {'class': 'srp-results'})
        results = auctions_list.find_all('li', recursive=False)

        auctions = {}
        for result in results:
            # Filter out sponsored results
            if result.find('div', attrs={'class': 'promoted-lv'}) or \
                    result.find('div', attrs={'class': 's-item__title--tagblock'}) or \
                    result.find('a', href=re.compile('.*pulsar.*')) or \
                    result.find('span', attrs={'class', re.compile('.*SPONSORED.*')}):
                continue

            try:
                auction_id = int(result.attrs['listingid'])
            except KeyError:
                print("Found a non-item. Skipping...")
                print(result.prettify())
                continue
            except ValueError:
                print("Could not convert auction ID {auction_id} to int")

            name = ' '.join(result.find('h3').find('a').find( \
                    text=True, recursive=False).split())
            # Strip tracking query parameters from the uri
            tracking_uri = result.find('h3').find('a').attrs['href']
            uri = urljoin(tracking_uri, urlparse(tracking_uri).path)

            auctions[auction_id] = SearchResult(name, uri)
        return auctions


    def __parse_search_page(self, soup):
        # Try various parsing methods until one works
        try:
            return self.__parse_2020_search_soup(soup)
        except Exception:
            raise ValueError('Could not parse web page')

    def _scrape_search_page(self, uri):
        soup = self._get_page(uri)
        return self.__parse_search_page(soup), soup.prettify()

    def _generate_search_uri(self, query_string, n_page):
        if not isinstance(n_page, int) or n_page < 1:
            raise ValueError('n_results must be an int, greater than 0')
    
        return self.base_search_uri.format(query_string, n_page)
