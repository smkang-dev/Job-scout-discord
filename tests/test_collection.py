import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import main as bot
from state import State


def page_html(page, ids, total=60, size=20, ads=(), category='기획/경영', hidden_it=False):
    cache = {'ROOT_QUERY': {}}
    args = {'filterBy': {'activityTypeID': '5'}, 'pagination': {'page': page, 'pageSize': size}}
    cache['ROOT_QUERY']['activities(' + json.dumps(args) + ')'] = {'totalCount': total, 'nodes': [{'__ref': f'Activity:{i}'} for i in ids]}
    cache['Category:1'] = {'name': 'AI/ML엔지니어', 'parent': {'__ref': 'Category:2'}}
    cache['Category:2'] = {'name': 'IT/개발'}
    rows = []
    for i in (*ads, *ids):
        cache[f'Activity:{i}'] = {'categories': [{'__ref': 'Category:1'}] if hidden_it else []}
        rows.append(f'<tr><td><p class="company-name">회사</p></td><td><a class="recruit-link" href="/activity/{i}"><p class="recruit-name">일반 신입 공채</p><p class="recruit-category">{category}</p></a></td></tr>')
    data = {'props': {'pageProps': {'__APOLLO_STATE__': cache}}}
    return '<table>' + ''.join(rows) + '</table><script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + '</script>'


class CollectionTests(unittest.TestCase):
    def collect(self, html, pages=5):
        session = Mock()
        session.get.side_effect = [Mock(status_code=200, text=x) for x in html]
        return bot.collect_jobs(session, pages, sleep=lambda _: None), session

    def test_multiple_pages_and_ads_deduplicate(self):
        jobs, session = self.collect([page_html(1,[101],ads=[99]),page_html(2,[102],ads=[99]),page_html(3,[103])])
        self.assertEqual(4, len(jobs))
        self.assertIn('page=2', session.get.call_args_list[1].args[0])
        self.assertEqual(3, session.get.call_count)

    def test_respects_page_cap(self):
        _, session = self.collect([page_html(1,[101],total=200)],1)
        self.assertEqual(1,session.get.call_count)

    def test_stops_at_explicit_last_page(self):
        _, session = self.collect([page_html(1,[101],total=1)])
        self.assertEqual(1,session.get.call_count)

    def test_verified_empty_page_is_valid(self):
        jobs,_ = self.collect([page_html(1,[],total=0)])
        self.assertEqual([],jobs)

    def test_wrong_page_fails(self):
        with self.assertRaises(bot.BotError):self.collect([page_html(1,[101]),page_html(1,[102])])

    def test_repeated_content_fails_even_if_number_changes(self):
        with self.assertRaises(bot.BotError):self.collect([page_html(1,[101]),page_html(2,[101])])

    def test_missing_metadata_fails(self):
        with self.assertRaises(bot.BotError):self.collect(['<table><tr><td class="company-name">회사</td><td><a class="recruit-link" href="/activity/1"><p class="recruit-name">제목</p></a></td></tr></table>'])

    def test_visible_category_matches_without_title_keywords(self):
        job=bot.parse_jobs(page_html(1,[101],category='기획/경영, IT/개발 외 1'))[0]
        self.assertEqual([],bot.matching_keywords(job.title))
        self.assertIn('직무:IT/개발',bot.match_reasons(job))

    def test_hidden_category_and_parent_match(self):
        job=bot.parse_jobs(page_html(1,[101],category='기획/경영 외 2',hidden_it=True))[0]
        self.assertIn('직무:IT/개발',bot.match_reasons(job))

    def test_unrelated_category_does_not_match(self):
        job=bot.parse_jobs(page_html(1,[101],category='바이오/제약연구개발'))[0]
        self.assertEqual([],bot.match_reasons(job))

    def test_corrupt_metadata_fails(self):
        with self.assertRaises(bot.BotError):bot.parse_page('<script id="__NEXT_DATA__">{bad</script>')

    def test_missing_regular_row_fails(self):
        html=page_html(1,[101]).replace('class="recruit-name"','class="changed"')
        with self.assertRaises(bot.BotError):bot.parse_page(html)

    def test_collection_failure_keeps_state_and_does_not_post(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'sent_jobs.json').write_text('[]')
            session=Mock()
            session.get.side_effect=[Mock(status_code=200,text=page_html(1,[101])),Mock(status_code=403)]
            with patch.object(bot.requests,'Session') as factory:
                factory.return_value.__enter__.return_value=session
                self.assertEqual(1,bot.main(['--dry-run','--state-dir',d]))
            session.post.assert_not_called()
            self.assertEqual('[]',(root/'sent_jobs.json').read_text())

    def test_previously_sent_category_candidate_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'sent_jobs.json').write_text('["https://linkareer.com/activity/101"]')
            state=State(root);session=Mock()
            jobs=bot.parse_jobs(page_html(1,[101],hidden_it=True))
            self.assertEqual(0,bot.process(jobs,state,session))
            session.post.assert_not_called()

    def test_message_explains_category_match(self):
        job=bot.parse_jobs(page_html(1,[101],hidden_it=True))[0]
        embed=bot.payload(job,bot.match_reasons(job))['embeds'][0]
        self.assertIn('직무:IT/개발',embed['fields'][0]['value'])
