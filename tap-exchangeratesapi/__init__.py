#!/usr/bin/env python3
import json
import sys
import argparse
import time
import requests
import singer
import backoff
import copy

from datetime import date, datetime, timedelta
base_url = 'https://api.apilayer.com/fixer/'

logger = singer.get_logger()
session = requests.Session()

DATE_FORMAT='%Y-%m-%d'


def parse_response(r):
    flattened = r['rates']
    flattened[r['base']] = 1.0
    flattened['date'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.strptime(r['date'], DATE_FORMAT))
    return flattened


schema = {'type': 'object',
          'properties':
          {'date': {'type': 'string',
                    'format': 'date-time'}}}


def giveup(error):
    logger.error(error.response.text)
    response = error.response
    return not (response.status_code == 429 or
                response.status_code >= 500)


@backoff.on_exception(backoff.constant,
                      (requests.exceptions.RequestException),
                      jitter=backoff.random_jitter,
                      max_tries=5,
                      giveup=giveup,
                      interval=30)
def request(url, params):
    response = requests.get(url=url, params=params)
    response.raise_for_status()
    return response


def do_sync(base, start_date, api_key):
    state = {'start_date': start_date}
    next_date = start_date
    prev_schema = {}

    if not api_key:
        logger.info('Missing api key in the request. Please re-configure and run again!')
        sys.exit(1)

    try:
        while datetime.strptime(next_date, DATE_FORMAT) <= datetime.utcnow():
            logger.info('Replicating exchange rate data from %s using base %s',
                        next_date,
                        base)

            rest_endpoint = base_url + str(next_date) + "?base=" + base
            response = requests.get(rest_endpoint, headers={"apikey": api_key})
            payload = response.json()

            # Parse the response if it is only 200 otherwise throw exception and exit
            if response.status_code != 200:
                logger.info('Response missing the expected elements! The api received %s from the endpoint.',
                            response.status_code)
                sys.exit(1)

            # Sort data set based on the currency value and update the response payload
            currencies_dictionary = payload['rates']
            sorted_dictionary = {currency: round(value, 2) for currency, value in sorted(currencies_dictionary.items(),
                                                                                         key=lambda item: item[1])}
            payload['rates'] = sorted_dictionary

            # Update schema if new currency/currencies exist
            for rate in payload['rates']:
                if rate not in schema['properties']:
                    schema['properties'][rate] = {'type': ['null', 'number']}

            # Only write schema if it has changed
            if schema != prev_schema:
                singer.write_schema('exchange_rate', schema, 'date')

            if payload['date'] == next_date:
                singer.write_records('exchange_rate', [parse_response(payload)])

            state = {'start_date': next_date}
            next_date = (datetime.strptime(next_date, DATE_FORMAT) + timedelta(days=1)).strftime(DATE_FORMAT)
            prev_schema = copy.deepcopy(schema)

    except requests.exceptions.RequestException as e:
        logger.fatal('Error on ' + e.request.url +
                     '; received status ' + str(e.response.status_code) +
                     ': ' + e.response.text)
        singer.write_state(state)
        sys.exit(-1)

    singer.write_state(state)
    logger.info('Tap exiting normally')


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '-c', '--config', help='Config file', required=False)
    parser.add_argument(
        '-s', '--state', help='State file', required=False)

    args = parser.parse_args()

    if args.config:
        with open(args.config) as file:
            config = json.load(file)
    else:
        config = {}

    if args.state:
        with open(args.state) as file:
            state = json.load(file)
    else:
        state = {}

    start_date = state.get('start_date') or config.get('start_date') or datetime.utcnow().strftime(DATE_FORMAT)
    start_date = singer.utils.strptime_with_tz(start_date).date().strftime(DATE_FORMAT)

    do_sync(config.get('base', 'USD'), start_date, config.get('api_key', None))


if __name__ == '__main__':
    main()

