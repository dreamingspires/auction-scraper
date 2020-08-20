from auction_scraper.scrapers.liveauctioneers.scraper import LiveAuctioneersAuctionScraper
s = LiveAuctioneersAuctionScraper('./app.db', './data')
auction = s.scrape_search_to_db('art', 1)
