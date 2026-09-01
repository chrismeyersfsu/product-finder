"""Built-in site registry: 21 marketplaces as pure data.

Owns the default site specs — nothing else. A site is
{slug, name, kind, config}; kind "css" config holds a search `url`
with a {query} placeholder plus CSS selectors (item/title/price/link,
optional link_attr and seller), kind "reddit_json" holds just `url`.
Never fetches, parses, or stores; callers copy these into the sites
table and may override any config there.

Selectors are best-effort snapshots of each site's public search page
and will rot as sites redesign. JS-heavy or bot-blocking sites
(amazon, walmart, target, bestbuy, backmarket, mercari, offerup,
shopgoodwill, govdeals) often return errors or empty results to plain
HTTP clients; run.search_site records that per site instead of failing
the run. Craigslist needs a region subdomain in its url.
"""


def _css(slug, name, url, item, title, price, link, link_attr="href", seller=None):
    config = {
        "url": url,
        "item": item,
        "title": title,
        "price": price,
        "link": link,
        "link_attr": link_attr,
    }
    if seller:
        config["seller"] = seller
    return {"slug": slug, "name": name, "kind": "css", "config": config}


# Not part of BUILTIN_SITES: eBay *sold/completed* listings, used to
# backfill real historical sale prices (eBay exposes roughly the last
# 90 days). Same css machinery; the "date" selector yields sold_at.
EBAY_SOLD = {
    "slug": "ebay-sold",
    "name": "eBay sold listings",
    "kind": "css",
    "config": {
        "url": "https://www.ebay.com/sch/i.html?_nkw={query}&LH_Sold=1&LH_Complete=1&_sop=13",
        "item": "li.s-item",
        "title": ".s-item__title",
        "price": ".s-item__price",
        "link": "a.s-item__link",
        "link_attr": "href",
        "seller": ".s-item__seller-info-text",
        "date": ".s-item__caption",
    },
}

BUILTIN_SITES = [
    _css(
        "ebay",
        "eBay",
        "https://www.ebay.com/sch/i.html?_nkw={query}&_sop=15",
        "li.s-item",
        ".s-item__title",
        ".s-item__price",
        "a.s-item__link",
        seller=".s-item__seller-info-text",
    ),
    _css(
        "craigslist",
        "Craigslist (set your region in config.url)",
        "https://sfbay.craigslist.org/search/sss?query={query}",
        "li.cl-static-search-result",
        "div.title",
        "div.price",
        "a",
    ),
    _css(
        "amazon",
        "Amazon",
        "https://www.amazon.com/s?k={query}",
        "div[data-component-type='s-search-result']",
        "h2 a span",
        "span.a-offscreen",
        "h2 a",
    ),
    _css(
        "newegg",
        "Newegg",
        "https://www.newegg.com/p/pl?d={query}",
        "div.item-cell",
        "a.item-title",
        "li.price-current",
        "a.item-title",
    ),
    _css(
        "bestbuy",
        "Best Buy",
        "https://www.bestbuy.com/site/searchpage.jsp?st={query}",
        "li.sku-item",
        "h4.sku-title a",
        "div.priceView-customer-price > span",
        "h4.sku-title a",
    ),
    _css(
        "walmart",
        "Walmart",
        "https://www.walmart.com/search?q={query}",
        "div[data-item-id]",
        "span[data-automation-id='product-title']",
        "div[data-automation-id='product-price']",
        "a[link-identifier]",
    ),
    _css(
        "target",
        "Target",
        "https://www.target.com/s?searchTerm={query}",
        "div[data-test='@web/site-top-of-funnel/ProductCardWrapper']",
        "a[data-test='product-title']",
        "span[data-test='current-price']",
        "a[data-test='product-title']",
    ),
    _css(
        "staples",
        "Staples",
        "https://www.staples.com/search?query={query}",
        "div.standard-type__product_card",
        "a.standard-type__product_title",
        "div.standard-type__price",
        "a.standard-type__product_title",
    ),
    _css(
        "officedepot",
        "Office Depot",
        "https://www.officedepot.com/catalog/search.do?Ntt={query}",
        "div.od-search-browse-products-item",
        "a.od-product-card-region-description",
        "span.od-graphql-price-big-price",
        "a.od-product-card-region-description",
    ),
    _css(
        "adorama",
        "Adorama",
        "https://www.adorama.com/l/?searchinfo={query}",
        "div.item",
        "div.item-details a",
        "div.prices span.your-price",
        "div.item-details a",
    ),
    _css(
        "bhphotovideo",
        "B&H Photo Video",
        "https://www.bhphotovideo.com/c/search?q={query}",
        "div[data-selenium='miniProductPage']",
        "span[data-selenium='miniProductPageProductName']",
        "span[data-selenium='uppedDecimalPrice']",
        "a[data-selenium='miniProductPageProductNameLink']",
    ),
    _css(
        "microcenter",
        "Micro Center",
        "https://www.microcenter.com/search/search_results.aspx?Ntt={query}",
        "li.product_wrapper",
        "div.pDescription a",
        "span.price",
        "div.pDescription a",
    ),
    _css(
        "backmarket",
        "Back Market",
        "https://www.backmarket.com/en-us/search?q={query}",
        "div[data-qa='productCard']",
        "h2",
        "div[data-qa='productCardPrice']",
        "a",
    ),
    _css(
        "swappa",
        "Swappa",
        "https://swappa.com/search?q={query}",
        "div.listing_row",
        "a.listing_title",
        "span.listing_price",
        "a.listing_title",
    ),
    _css(
        "mercari",
        "Mercari",
        "https://www.mercari.com/search/?keyword={query}",
        "div[data-testid='ItemCell']",
        "div[data-testid='ItemName']",
        "div[data-testid='ItemPrice']",
        "a",
    ),
    _css(
        "offerup",
        "OfferUp",
        "https://offerup.com/search?q={query}",
        "a[title]",
        "span.MuiTypography-subtitle1",
        "span.MuiTypography-body2",
        "a[title]",
        link_attr="href",
    ),
    _css(
        "bonanza",
        "Bonanza",
        "https://www.bonanza.com/items/search?q%5Bsearch_term%5D={query}",
        "div.search_result_item",
        "div.item_title a",
        "div.item_price",
        "div.item_title a",
    ),
    _css(
        "woot",
        "Woot",
        "https://www.woot.com/search?q={query}",
        "div.v2-offer",
        "div.v2-offer-title",
        "span.v2-offer-price",
        "a.v2-offer-link",
    ),
    _css(
        "shopgoodwill",
        "ShopGoodwill",
        "https://shopgoodwill.com/categories/listing?st={query}",
        "div.feat-item",
        "div.feat-item_name",
        "div.feat-item_price",
        "a",
    ),
    _css(
        "govdeals",
        "GovDeals",
        "https://www.govdeals.com/search?kWord={query}",
        "div.asset-card",
        "a.asset-title",
        "span.asset-price",
        "a.asset-title",
    ),
    {
        "slug": "reddit-hardwareswap",
        "name": "r/hardwareswap",
        "kind": "reddit_json",
        "config": {
            "url": "https://www.reddit.com/r/hardwareswap/search.json"
            "?q={query}&restrict_sr=on&sort=new&limit=50"
        },
    },
]
