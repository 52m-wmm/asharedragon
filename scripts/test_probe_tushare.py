"""Offline safety/format tests; no provider calls, credentials or database access."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec=importlib.util.spec_from_file_location('probe_tushare',Path(__file__).with_name('probe_tushare.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher=patch.object(m,'ROOT',Path(self.directory.name));patcher.start();self.addCleanup(patcher.stop)

    def test_supported_shapes_and_malformed_not_empty(self):
        self.assertEqual(m.parse_rows({'code':0,'data':{'fields':['x'],'items':[[0]]}}),(['x'],[{'x':0}]))
        self.assertEqual(m.parse_rows({'result':{'records':[{'x':1}]}}),(['x'],[{'x':1}]))
        with self.assertRaises(ValueError):m.parse_rows({'code':0})
        with self.assertRaises(ValueError):m.parse_rows({'data':{'fields':['x'],'items':[[1,2]]}})

    def test_classification(self):
        self.assertEqual(m.classify_error(200,2002,'权限不足'),'permission_denied')
        self.assertEqual(m.classify_error(429,None,''),'rate_limited')
        self.assertEqual(m.classify_error(200,-1,'每分钟最多调用'),'rate_limited')
        self.assertIsNone(m.classify_error(200,0,None))
        self.assertFalse(m.missing(0))

    def test_tls_failure_and_http_redirect_never_authenticate(self):
        p=m.Probe('http://example.test/api','fixture-secret',interval=0)
        r=Mock(status_code=302,is_redirect=True)
        p.call=Mock(side_effect=[(None,'tls_verification_failed',.1),(r,None,.1)])
        p.preflight(accept_http=True)
        self.assertIsNone(p.transport)
        self.assertTrue(p.block)
        self.assertEqual(len(p.call.call_args_list[0].args),1)
        self.assertEqual(p.call.call_args_list[1].args[1]['token'],'')

    def test_no_http_fallback_without_approval(self):
        p=m.Probe('http://example.test/api','fixture-secret',interval=0)
        p.call=Mock(side_effect=[(None,'tls_verification_failed',.1),(Mock(status_code=200,is_redirect=False),None,.1)])
        p.preflight()
        self.assertIsNone(p.transport)

    def test_rate_limit_wait_budget_and_redaction(self):
        p=m.Probe('https://example.test/api','fixture-secret',interval=0,max_requests=2)
        p.transport=p.url
        a=Mock(status_code=429,is_redirect=False);a.json.return_value={'code':429,'msg':'rate limit fixture-secret'}
        b=Mock(status_code=200,is_redirect=False);b.json.return_value={'code':0,'data':{'fields':['trade_date','total_mv'],'items':[['20261102',0]]}}
        p.session.request=Mock(side_effect=[a,b])
        with patch.object(m.time,'sleep') as sleep:
            r=p.query('daily_basic',{'trade_date':'20261102'})
        sleep.assert_any_call(60)
        self.assertEqual(p.used,2)
        self.assertEqual(r['status'],'success')
        self.assertEqual(r['missing_rates']['total_mv']['rate'],0)
        self.assertEqual(r['missing_rates']['pe_ttm']['rate'],1)
        for call in p.session.request.call_args_list:
            self.assertFalse(call.kwargs['allow_redirects']);self.assertTrue(call.kwargs['verify'])
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'report.md';m.write_report(p,path)
            self.assertNotIn('fixture-secret',path.read_text())
        with self.assertRaises(RuntimeError):p.call(p.url)

    def test_empty_not_permission_and_wrong_date_not_success(self):
        p=m.Probe('https://example.test/api','',interval=0)
        p.transport=p.url
        a=Mock(status_code=200,is_redirect=False);a.json.return_value={'code':0,'data':{'fields':['trade_date'],'items':[]}}
        b=Mock(status_code=200,is_redirect=False);b.json.return_value={'code':0,'data':[{'trade_date':'20251102'}]}
        p.session.request=Mock(side_effect=[a,b])
        self.assertEqual(p.query('limit_list_d',{'trade_date':'20261102'})['status'],'empty_unverified')
        self.assertEqual(p.query('limit_list_d',{'trade_date':'20261102'})['status'],'quality_unverified')

if __name__=='__main__':unittest.main()
