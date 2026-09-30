import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import requests
import main as bot
from state import State, StateError, atomic_write, canonical_url, state_lock

URL = "https://linkareer.com/activity/101"
URL2 = "https://linkareer.com/activity/102"
JOB = bot.Job("테스트 회사", "Java 백엔드 개발자 채용", URL)
ENDPOINT = "https://discord.com/api/webhooks/123/test-token?wait=true"


def card(url, title="Java 백엔드 개발자 채용", company="테스트 회사"):
    return f'<tr><td><p class="company-name">{company}</p></td><td><a class="recruit-link" href="{url}"><span>추천</span><p class="recruit-name">{title}</p><p class="recruit-category">IT/개발</p></a></td></tr>'


HTML = (
    "<table>"
    + card("/activity/101?x=1")
    + card("/activity/101#part")
    + card("https://evil.example/activity/102")
    + "</table>"
)


def response(status=200, data=None, text=""):
    r = Mock(status_code=status, headers={}, text=text)
    r.json.return_value = data
    return r


class BotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sent = self.root / "sent_jobs.json"
        self.sent.write_text("[]\n", encoding="utf-8")
        self.state = State(self.root)
        self.session = Mock()
        self.sleep = Mock()

    def send(self, jobs=None):
        return bot.process(
            jobs or [JOB], self.state, self.session, "send", ENDPOINT, self.sleep
        )

    def test_canonical_url_and_foreign_hosts(self):
        self.assertEqual(URL, canonical_url("/activity/00101/?x=1#part"))
        for v in [
            "https://evil.example/activity/101",
            "/list/recruit",
            "javascript:alert(1)",
            None,
            "https://user@linkareer.com/activity/1",
        ]:
            self.assertIsNone(canonical_url(v))

    def test_parser_deduplicates(self):
        self.assertEqual([JOB], bot.parse_jobs(HTML))

    def test_category_and_recommendation_are_not_title(self):
        job = bot.parse_jobs(
            "<table>"
            + card("/activity/103", "일반 신입사원 모집", "실제회사")
            + "</table>"
        )[0]
        self.assertEqual("실제회사", job.company)
        self.assertEqual("일반 신입사원 모집", job.title)
        self.assertEqual([], bot.matching_keywords(job.title))

    def test_missing_company_fails_instead_of_guessing(self):
        html = '<table><tr><td><a class="recruit-link" href="/activity/101"><p class="recruit-name">Java 개발자</p></a></td></tr></table>'
        with self.assertRaises(bot.BotError):
            bot.parse_jobs(html)

    def test_empty_or_changed_html_fails(self):
        for html in [
            "",
            "<html>Access denied</html>",
            '<a href="/activity/1">image</a>',
        ]:
            with self.assertRaises(bot.BotError):
                bot.parse_jobs(html)

    def test_keywords_avoid_ascii_substrings(self):
        self.assertEqual([], bot.matching_keywords("Digital designer JavaScript"))
        self.assertEqual(
            ["it", "db", "java", "python"],
            bot.matching_keywords("IT직군 DB/Java Python 개발"),
        )
        self.assertEqual(
            ["백엔드", "backend"], bot.matching_keywords("백엔드 BACKEND 개발자")
        )

    def test_corrupt_state_does_not_reset(self):
        for content in ["{oops", "{}", "[null]", '["https://evil.example/activity/1"]']:
            self.sent.write_text(content, encoding="utf-8")
            with self.assertRaises(StateError):
                State(self.root)
            self.assertEqual(content, self.sent.read_text(encoding="utf-8"))

    def test_missing_state_stops(self):
        self.sent.unlink()
        with self.assertRaises(StateError):
            State(self.root)

    def test_bom_and_old_urls_compatible(self):
        self.sent.write_text(json.dumps([URL + "?x=1", URL]), encoding="utf-8-sig")
        self.assertEqual({URL}, State(self.root).sent)

    def test_atomic_write_failure_preserves_original(self):
        before = self.sent.read_bytes()
        with patch("state.os.replace", side_effect=OSError()):
            with self.assertRaises(OSError):
                atomic_write(self.sent, {URL})
        self.assertEqual(before, self.sent.read_bytes())
        self.assertEqual([], list(self.root.glob("*.tmp")))

    def test_local_lock(self):
        with state_lock(self.root):
            with self.assertRaises(StateError):
                with state_lock(self.root):
                    pass
        self.assertFalse((self.root / ".job-scout.lock").exists())

    def test_dry_run_has_no_side_effects(self):
        before = self.sent.read_bytes()
        self.assertEqual(1, bot.process([JOB], self.state, self.session))
        self.session.post.assert_not_called()
        self.assertEqual(before, self.sent.read_bytes())
        self.assertFalse(self.state.pending_file.exists())

    def test_baseline_does_not_send(self):
        bot.process([JOB], self.state, self.session, "baseline")
        self.session.post.assert_not_called()
        self.assertEqual({URL}, State(self.root).sent)

    def test_success_recorded_and_not_resent(self):
        self.session.post.return_value = response(200, {"id": "123"})
        self.send()
        state = State(self.root)
        self.assertEqual({URL}, state.sent)
        self.assertFalse(state.pending)
        bot.process([JOB], state, self.session, "send", ENDPOINT, self.sleep)
        self.assertEqual(1, self.session.post.call_count)
        self.assertEqual(bot.TIMEOUT, self.session.post.call_args.kwargs["timeout"])
        self.assertFalse(self.session.post.call_args.kwargs["allow_redirects"])

    def test_definite_rejections_remain_unsent(self):
        for status in [400, 401, 403, 404]:
            self.session.post.return_value = response(status)
            with self.assertRaises(bot.DeliveryRejected):
                self.send()
            self.assertFalse(State(self.root).sent)
            self.assertFalse(State(self.root).pending)

    def test_timeout_retained_without_retry_or_secret_leak(self):
        self.session.post.side_effect = requests.Timeout("secret-token")
        with self.assertRaises(bot.DeliveryUnknown) as e:
            self.send()
        self.assertNotIn("secret-token", str(e.exception))
        state = State(self.root)
        self.assertEqual({URL}, state.pending)
        self.assertFalse(state.sent)
        with self.assertRaises(bot.BotError):
            bot.process([JOB], state, self.session, "send", ENDPOINT, self.sleep)
        self.assertEqual(1, self.session.post.call_count)

    def test_server_error_not_retried(self):
        self.session.post.return_value = response(503)
        with self.assertRaises(bot.DeliveryUnknown):
            self.send()
        self.assertEqual(1, self.session.post.call_count)
        self.assertEqual({URL}, State(self.root).pending)

    def test_success_without_message_id_is_pending(self):
        self.session.post.return_value = response(200, {})
        with self.assertRaises(bot.DeliveryUnknown):
            self.send()
        self.assertEqual({URL}, State(self.root).pending)

    def test_rate_limit_waits_and_succeeds(self):
        self.session.post.side_effect = [
            response(429, {"retry_after": 1.25}),
            response(200, {"id": "1"}),
        ]
        self.send()
        self.sleep.assert_called_once_with(1.35)
        self.assertEqual({URL}, State(self.root).sent)

    def test_rate_limit_bounded_attempts(self):
        self.session.post.return_value = response(429, {"retry_after": 0})
        with self.assertRaises(bot.DeliveryRejected):
            self.send()
        self.assertEqual(3, self.session.post.call_count)
        self.assertEqual(2, self.sleep.call_count)
        self.assertFalse(State(self.root).pending)

    def test_invalid_or_long_wait_not_shortened(self):
        for delay in [100, -1, "NaN", None]:
            self.session.post.return_value = response(429, {"retry_after": delay})
            with self.assertRaises(bot.DeliveryRejected):
                self.send()
        self.sleep.assert_not_called()

    def test_pending_saved_before_post(self):
        def post(*args, **kwargs):
            self.assertEqual({URL}, State(self.root).pending)
            return response(200, {"id": "1"})

        self.session.post.side_effect = post
        self.send()

    def test_partial_delivery_preserved(self):
        self.session.post.side_effect = [response(200, {"id": "1"}), requests.Timeout()]
        with self.assertRaises(bot.DeliveryUnknown):
            self.send([JOB, bot.Job("회사2", "Python 개발자 채용", URL2)])
        state = State(self.root)
        self.assertEqual({URL}, state.sent)
        self.assertEqual({URL2}, state.pending)

    def test_crash_after_sent_write_does_not_duplicate(self):
        self.state.begin(URL)
        atomic_write(self.state.sent_file, {URL})
        state = State(self.root)
        self.assertEqual({URL}, state.sent)
        self.assertFalse(state.pending)

    def test_get_retries(self):
        self.session.get.side_effect = [
            requests.Timeout(),
            response(503),
            response(200, text=HTML),
        ]
        self.assertEqual(HTML, bot.fetch_html(self.session, self.sleep))
        self.assertEqual(3, self.session.get.call_count)

    def test_forbidden_get_stops(self):
        self.session.get.return_value = response(403)
        with self.assertRaises(bot.BotError):
            bot.fetch_html(self.session, self.sleep)
        self.assertEqual(1, self.session.get.call_count)

    def test_webhook_validation(self):
        self.assertEqual(
            ENDPOINT,
            bot.webhook_url(
                "https://discord.com/api/webhooks/123/test-token?wait=false"
            ),
        )
        for v in [
            None,
            "http://discord.com/api/webhooks/1/x",
            "https://evil.example/api/webhooks/1/x",
        ]:
            with self.assertRaises(bot.BotError):
                bot.webhook_url(v)

    def test_payload_limits_and_mentions(self):
        p = bot.payload(bot.Job("😀" * 300, "가" * 5000, URL), ["java"])
        self.assertEqual([], p["allowed_mentions"]["parse"])
        e = p["embeds"][0]
        self.assertLessEqual(len(e["title"].encode("utf-16-le")) // 2, 256)
        self.assertLessEqual(len(e["description"]), 3500)

    def test_manual_resolution_without_network(self):
        self.state.begin(URL)
        with patch("main.requests.Session") as session:
            self.assertEqual(
                0, bot.main(["--state-dir", str(self.root), "--resolve-sent", URL])
            )
            session.assert_not_called()
        self.assertEqual({URL}, State(self.root).sent)

    def test_manual_retry_only_releases_pending(self):
        self.state.begin(URL)
        with patch("main.requests.Session") as session:
            self.assertEqual(
                0, bot.main(["--state-dir", str(self.root), "--resolve-retry", URL])
            )
            session.assert_not_called()
        state = State(self.root)
        self.assertFalse(state.sent)
        self.assertFalse(state.pending)


if __name__ == "__main__":
    unittest.main()
