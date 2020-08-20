from auction_scraper.scrapers.liveauctioneers.scraper import LiveAuctioneersAuctionScraper
s = LiveAuctioneersAuctionScraper('./app.db', './data')
auction = s.scrape_auction_to_db('https://www.liveauctioneers.com/item/88614578_keith-haring-man-diving-drawing')
