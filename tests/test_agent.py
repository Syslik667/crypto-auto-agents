import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import agent


class AgentTests(unittest.TestCase):
    def test_rss_extracts_opportunity_and_flags_deposit(self):
        raw = b'''<rss><channel><item><title>Earn reward by deposit and trading volume</title>
          <link>https://example.org/post</link><description>Bonus campaign</description><pubDate>today</pubDate>
          </item><item><title>Regular exchange maintenance</title><link>https://example.org/other</link></item>
          </channel></rss>'''
        result = agent.parse_feed(raw, 'test')
        self.assertEqual(len(result), 1)
        self.assertIn('платные', result[0]['status'])

    def test_atom_entry_link(self):
        raw = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Learn &amp; Earn BTC Quiz</title><link href="https://example.org/quiz"/></entry></feed>'''
        out = agent.parse_feed(raw, 'atom')
        self.assertEqual(out[0]['url'], 'https://example.org/quiz')

    def test_html_escaping(self):
        payload = {'time_utc': 'test', 'market': [], 'health': {'x': 'OK'}, 'new': [
            {'title': '<script>alert(1)</script>', 'url':'https://example.org', 'source':'x',
             'published':'today', 'status':'unverified'}], 'latest': []}
        result = agent.build_html(payload)
        self.assertNotIn('<script>alert(1)</script>', result)
        self.assertIn('&lt;script&gt;', result)

    def test_market_parsing(self):
        sample=b'[ {"id":"bitcoin","name":"Bitcoin","symbol":"btc","current_price":1000,"price_change_percentage_24h":-4.25} ]'
        with patch.object(agent, 'fetch', return_value=sample):
            result = agent.get_market_data()
        self.assertEqual(result[0]['symbol'], 'BTC')
        self.assertEqual(result[0]['change24h'], -4.25)


if __name__ == '__main__':
    unittest.main()
