from auction_scraper.scrapers.liveauctioneers.scraper import LiveAuctioneersAuctionScraper
s = LiveAuctioneersAuctionScraper('./app.db', './data')
auction = s.scrape_profile_to_db('https://www.liveauctioneers.com/auctioneer/197/hindman/')
