# -*- coding: utf-8 -*-

import threading
import json
import datetime
import random
import logging
import socket
from urllib.parse import urlencode

import GoogleScraper.socks as socks
from GoogleScraper.scraping import SearchEngineScrape, get_base_search_url_by_search_engine, StopScrapingException
from GoogleScraper.parsing import get_parser_by_search_engine
from GoogleScraper.config import Config
from GoogleScraper.log import out
from GoogleScraper.user_agents import user_agents

logger = logging.getLogger('GoogleScraper')

headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
}

def get_GET_params_for_search_engine(query, search_engine, page_number=1, num_results_per_page=50, search_type='normal'):
    """Returns the params of the url for the search engine and the search mode.

    Args:
        search_engine: The search engine. Example: 'google'
        search_mode: The search mode. Example: 'image' or 'normal'
        query: The search query
        page_number: Which SERP page.
        num_results_per_page: How many entries per page.

    Returns:
        The params for the GET url.
    """
    
    search_params = {}

    # Don't set the offset parameter explicitly if the default search (no offset) is correct.
    start_search_position = None if page_number == 1 else str(int(num_results_per_page) * int(page_number))

    if search_engine == 'google':
        search_params['q'] = query
        search_params['num'] = str(num_results_per_page)
        search_params['start'] = start_search_position

        if search_type == 'image':
            search_params.update({
                'oq': query,
                'site': 'imghp',
                'tbm': 'isch',
                'source': 'hp',
                # 'sa': 'X',
                'biw': 1920,
                'bih': 881
            })
        elif search_type == 'video':
            search_params.update({
                'tbm': 'vid',
                'source': 'lnms',
                'sa': 'X',
                'biw': 1920,
                'bih': 881
            })
        elif search_type == 'news':
            search_params.update({
                'tbm': 'nws',
                'source': 'lnms',
                'sa': 'X'
            })

    elif search_engine == 'yandex':
        search_params['text'] = query
        search_params['p'] = start_search_position

        if search_type == 'image':
            base_search_url = 'http://yandex.ru/images/search?'

    elif search_engine == 'bing':
        search_params['q'] = query
        search_params['first'] = start_search_position

    elif search_engine == 'yahoo':
        search_params['p'] = query
        search_params['b'] = start_search_position
        search_params['ei'] = 'UTF-8'

    elif search_engine == 'baidu':
        search_params['wd'] = query
        search_params['pn'] = start_search_position
        search_params['ie'] = 'utf-8'
    elif search_engine == 'duckduckgo':
        search_params['q'] = query
    elif search_engine == 'ask':
        search_params['q'] = query
        search_params['qsrc'] = '0'
        search_params['l'] = 'dir'
        search_params['qo'] = 'homepageSearchBox'
    elif search_engine == 'blekko':
        search_params['q'] = query

    return search_params


class HttpScrape(SearchEngineScrape, threading.Timer):
    """Offers a fast way to query any search engine using raw HTTP requests.

    Overrides the run() method of the superclass threading.Timer.
    Each thread represents a crawl for one Search Engine SERP page. Inheriting
    from threading.Timer allows the deriving class to delay execution of the run()
    method.

    This is a base class, Any supported search engine needs to subclass HttpScrape to
    implement this specific scrape type.

    Attributes:
        results: Returns the found results.
    """

    def __init__(self, *args, time_offset=0.0, **kwargs):
        """Initialize an HttScrape object to scrape over blocking http.

        HttpScrape inherits from SearchEngineScrape
        and from threading.Timer.
        """
        threading.Timer.__init__(self, time_offset, self.search)
        SearchEngineScrape.__init__(self, *args, **kwargs)
        
        # Bind the requests module to this instance such that each 
        # instance may have an own proxy
        self.requests = __import__('requests')
        
        # initialize the GET parameters for the search request
        self.search_params = {}

        # initialize the HTTP headers of the search request
        # to some base values that mozilla uses with requests.
        # the Host and User-Agent field need to be set additionally.
        self.headers = headers

        # the mode
        self.scrape_method = 'http'

        # get the base search url based on the search engine.
        self.base_search_url = get_base_search_url_by_search_engine(self.search_engine_name, self.scrape_method)

        super().instance_creation_info(self.__class__.__name__)

        if self.search_engine_name == 'blekko':
            logger.critical('blekko doesnt support http mode.')
            self.startable = False


    def set_proxy(self):
        """Setup a socks connection for the socks module bound to this instance.

        Args:
            proxy: Namedtuple, Proxy to use for this thread.
        """
        def create_connection(address, timeout=None, source_address=None):
            sock = socks.socksocket()
            sock.connect(address)
            return sock

        pmapping = {
            'socks4': 1,
            'socks5': 2,
            'http': 3
        }
        # Patch the socket module
        # rdns is by default on true. Never use rnds=False with TOR, otherwise you are screwed!
        socks.setdefaultproxy(pmapping.get(self.proxy.proto), self.proxy.host, int(self.proxy.port), rdns=True)
        socks.wrap_module(socket)
        socket.create_connection = create_connection

    def switch_proxy(self, proxy):
        super().switch_proxy()

    def proxy_check(self):
        assert self.proxy and self.requests, 'ScraperWorker needs valid proxy instance and requests library to make the proxy check.'

        online = False
        status = 'Proxy check failed: {host}:{port} is not used while requesting'.format(**self.proxy.__dict__)
        ipinfo = {}

        try:
            text = self.requests.get(Config['GLOBAL'].get('proxy_info_url')).text
            try:
                ipinfo = json.loads(text)
            except ValueError as v:
                pass
        except self.requests.ConnectionError as e:
            status = 'No connection to proxy server possible, aborting: {}'.format(e)
        except self.requests.Timeout as e:
            status = 'Timeout while connecting to proxy server: {}'.format(e)
        except self.requests.exceptions.RequestException as e:
            status = 'Unknown exception: {}'.format(e)

        if 'ip' in ipinfo and ipinfo['ip']:
            online = True
            status = 'Proxy is working.'
        else:
            logger.warning(status)

        super().update_proxy_status(status, ipinfo, online)

        return online


    def handle_request_denied(self, status_code=''):
        """Handle request denied by the search engine.

        This is the perfect place to distinguish the different responses
        if search engine detect exhaustive searching.

        Args:
            status_code: The status code of the HTTP response.

        Returns:
        """
        super().handle_request_denied(status_code)

    def build_search(self):
        """Build the headers and params for the search request for the search engine."""

        self.search_params = get_GET_params_for_search_engine(self.query, self.search_engine_name,
                                                              self.page_number, self.num_results_per_page,
                                                              self.search_type)

        self.parser = get_parser_by_search_engine(self.search_engine_name)
        self.parser = self.parser()

    def search(self, *args, rand=False, **kwargs):
        """The actual search for the search engine.

        When raising StopScrapingException, the scraper will stop.

        When return False, the scraper tries to continue with next keyword.
        """

        self.build_search()

        if rand:
            self.headers['User-Agent'] = random.choice(user_agents)

        try:
            super().detection_prevention_sleep()
            super().keyword_info()

            request = self.requests.get(self.base_search_url + urlencode(self.search_params), headers=self.headers, timeout=5)

            self.requested_at = datetime.datetime.utcnow()
            self.html = request.text

            out('[HTTP - {url}, headers={headers}, params={params}'.format(
                url=request.url,
                headers=self.headers,
                params=self.search_params),
            lvl=3)

        except self.requests.ConnectionError as ce:
            reason = 'Network problem occurred {}'.format(ce)
            raise StopScrapingException('Stopping scraping because {}'.format(reason))
        except self.requests.Timeout as te:
            reason = 'Connection timeout {}'.format(te)
            raise StopScrapingException('Stopping scraping because {}'.format(reason))
        except self.requests.exceptions.RequestException as e:
            # In case of any http networking exception that wasn't caught
            # in the actual request, just end the worker.
            raise StopScrapingException('Stopping scraping because {}'.format(e))

        if not request.ok:
            self.handle_request_denied(request.status_code)
            return False

        super().after_search()

        return True

    def run(self):
        super().before_search()

        if self.startable:
            args = []
            kwargs = {}
            kwargs['rand'] = False
            SearchEngineScrape.blocking_search(self, self.search, *args, **kwargs)
