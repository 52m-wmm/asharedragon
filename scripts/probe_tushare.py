"""Bounded third-party Tushare probe. Never imports application code or writes SQL.

Run with .venv/bin/python scripts/probe_tushare.py; credentials come only from
TUSHARE_API_URL/TUSHARE_TOKEN in environment or the ignored project .env.
HTTP authentication requires explicit --accept-insecure-http after user approval.
"""
import argparse
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import requests
from app.providers.tushare_rate import GlobalLimiter, RateStopped
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
KEYS = {
    'trade_cal': ['cal_date', 'is_open'],
    'limit_list_d': ['first_time', 'last_time', 'open_times', 'up_stat', 'limit_times', 'limit_amount', 'float_mv', 'fd_amount'],
    'kpl_list': ['lu_time', 'lu_desc', 'theme', 'status', 'limit_order'],
    'limit_list_ths': ['trade_date', 'ts_code', 'lu_desc', 'status'],
    'daily_basic': ['total_mv', 'circ_mv', 'pe_ttm', 'pb', 'turnover_rate'],
    'stk_limit': ['up_limit', 'down_limit'],
    'stock_st': ['ts_code', 'trade_date', 'type'],
    'limit_cpt_list': ['ts_code', 'trade_date', 'up_nums', 'rank'],
}
CAPS = {'limit_list_d': 2500, 'kpl_list': 8000, 'limit_list_ths': 4000,
        'daily_basic': 6000, 'stk_limit': 5800, 'stock_st': 1000, 'limit_cpt_list': 2000}
DOCS = {'trade_cal': 26, 'limit_list_d': 298, 'kpl_list': 347, 'limit_list_ths': 355,
        'daily_basic': 32, 'stk_limit': 183, 'stock_st': 397, 'limit_cpt_list': 357}


def sanitize(value, token=''):
    if isinstance(value, dict):
        return {str(k): '[REDACTED]' if re.search(r'token|password|secret|authorization|api.?key', str(k), re.I)
                else sanitize(v, token) for k, v in value.items()}
    if isinstance(value, list): return [sanitize(v, token) for v in value]
    if isinstance(value, str):
        if token: value = value.replace(token, '[REDACTED]')
        value = re.sub(r'\b[A-Za-z0-9_-]{48,}\b', '[REDACTED_LONG_ID]', value)
        value = re.sub(r'https?://[^\s\"<>]+', '[URL_REDACTED]', value)
        return value[:1000]
    return value


def preserve_evidence(root, api, params, records, token):
    """Private full evidence is separate from redacted public report samples."""
    import hashlib
    payload={'endpoint':api,'params':sanitize(params,token),'records':sanitize(records,token),
             'fetched_at':datetime.now(timezone.utc).isoformat(),'source':'tushare_proxy'}
    content=json.dumps(payload,ensure_ascii=False,sort_keys=True)
    directory=Path(root)/'data/tushare_probe_archive'/api;directory.mkdir(parents=True,exist_ok=True)
    path=directory/(hashlib.sha256(content.encode()).hexdigest()+'.json')
    temporary=path.with_suffix('.tmp');temporary.write_text(content);temporary.replace(path)
    return path


def parse_rows(payload):
    """Accept standard fields/items and explicit record-list response envelopes."""
    data = payload.get('data', payload.get('result', payload)) if isinstance(payload, dict) else payload
    if isinstance(data, dict) and 'fields' in data and 'items' in data:
        fields, items = data['fields'], data['items']
        if not isinstance(fields, list) or not all(isinstance(f, str) for f in fields):
            raise ValueError('invalid fields')
        if len(set(fields)) != len(fields) or not isinstance(items, list): raise ValueError('invalid table')
        if any(not isinstance(row, list) or len(row) != len(fields) for row in items):
            raise ValueError('row width differs from fields')
        return fields, [dict(zip(fields, row)) for row in items]
    if isinstance(data, dict):
        data = next((data[k] for k in ('records', 'rows', 'items', 'list') if k in data), None)
    if not isinstance(data, list) or any(not isinstance(r, dict) for r in data):
        raise ValueError('unrecognized response shape; not assumed empty')
    return list(dict.fromkeys(k for row in data for k in row)), data


def missing(v):
    return v is None or (isinstance(v, str) and v.strip().lower() in ('', 'null', 'none', 'nan', '--')) or (isinstance(v, float) and math.isnan(v))


def classify_error(http, code, message):
    msg = str(message).lower()
    if http == 429 or any(s in msg for s in ('每分钟', '频率', '频次', 'rate limit', 'too many')): return 'rate_limited'
    if http in (401, 403) or str(code) == '2002' or any(s in msg for s in ('权限', '积分', '无权', 'token无效', 'invalid token', 'permission', 'unauthorized')):
        return 'permission_denied'
    if http >= 400 or code not in (None, 0, '0', 200, '200'): return 'api_error'
    return None


def cases():
    day = '20261102'
    base = [('trade_cal', {'exchange': 'SSE', 'start_date': day, 'end_date': day})]
    base += [('limit_list_d', {'trade_date': day, 'limit_type': tag}) for tag in ('U', 'D', 'Z')]
    base += [('kpl_list', {'trade_date': day, 'tag': tag}) for tag in ('涨停', '炸板')]
    base += [('limit_list_ths', {'trade_date': day, 'limit_type': tag}) for tag in ('涨停池', '炸板池')]
    base += [(api, {'trade_date': day}) for api in ('daily_basic', 'stk_limit', 'stock_st', 'limit_cpt_list')]
    history = [('limit_list_d', {'trade_date': d, 'limit_type': 'U'}) for d in ('20201201', '20220513', '20230928', '20241104', '20250901')]
    history += [('daily_basic', {'trade_date': '20201201'})]
    return base, history


class Probe:
    def __init__(self, url, token, timeout=20, interval=1, retries=1, max_requests=20):
        self.url, self.token = url, token
        self.timeout, self.interval, self.retries = timeout, interval, retries
        self.max_requests, self.used = max_requests, 0
        self.network, self.results = [], []
        self.session = requests.Session()
        self.session.trust_env = False  # No implicit netrc credentials or proxy overrides.
        self.session.headers['User-Agent'] = 'AShareDragon-API-Probe/0.1'
        self.transport = None
        self.block = None
        self.limiter=GlobalLimiter(ROOT/"data/tushare_scheduler",sleep=lambda seconds: time.sleep(seconds))

    def call(self, url, payload=None):
        if self.used >= self.max_requests: raise RuntimeError('request_budget_exhausted')
        if self.used: time.sleep(self.interval)
        self.used += 1
        start = time.monotonic()
        try:
            r = self.limiter.perform(lambda: self.session.request('GET' if payload is None else 'POST', url, json=payload,
                                     timeout=(self.timeout, self.timeout), allow_redirects=False, verify=True),
                                     (payload or {}).get('api_name','preflight'),(payload or {}).get('params',{}),self.interval)
            return r, None, round(time.monotonic()-start, 3)
        except RateStopped:
            raise RuntimeError('three consecutive IP limits; operator review required')
        except requests.exceptions.SSLError:
            return None, 'tls_verification_failed', round(time.monotonic()-start, 3)
        except requests.exceptions.RequestException as exc:
            # Never serialize requests/exceptions: may contain body, URL or credentials.
            return None, 'network_error:' + type(exc).__name__, round(time.monotonic()-start, 3)

    def preflight(self, accept_http=False):
        u = urlsplit(self.url)
        if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.query or u.fragment:
            self.block = 'invalid_url_or_embedded_credentials'; return
        secure = urlunsplit(('https', u.netloc, u.path, '', ''))
        r, error, elapsed = self.call(secure)
        self.network.append({'scheme': 'https', 'authenticated': False, 'http_code': r.status_code if r is not None else None,
                             'status': error or 'tls_verified', 'seconds': elapsed,
                             'redirect': bool(r is not None and r.is_redirect)})
        if r is not None and not r.is_redirect:
            self.transport = secure; return
        # GET may be blocked even when JSON POST works. Probe with an empty token.
        # Never follow redirects or send a real credential during preflight.
        if u.scheme == 'http' and self.used < self.max_requests:
            r, error, elapsed = self.call(self.url, {'api_name': 'trade_cal', 'token': '',
                'params': {'exchange': 'SSE', 'start_date': '20261102', 'end_date': '20261102'}, 'fields': ''})
            self.network.append({'scheme': 'http', 'authenticated': False, 'http_code': r.status_code if r is not None else None,
                                 'status': error or 'reachable_unencrypted', 'seconds': elapsed,
                                 'redirect': bool(r is not None and r.is_redirect)})
            if accept_http and r is not None and not r.is_redirect:
                self.transport = self.url; return
        self.block = 'secure_transport_not_verified; authentication_paused; redirects_not_followed'

    def query(self, api, params):
        result = {'api': api, 'params': params, 'date': params.get('trade_date', params.get('start_date')),
                  'status': 'not_tested', 'http_code': None, 'server_code': None, 'error': None,
                  'rows': None, 'fields': [], 'missing_rates': {}, 'samples': [], 'attempts': []}
        self.results.append(result)
        for attempt in range(self.retries+1):
            if self.used >= self.max_requests:
                if not result['attempts']: result['status'] = 'budget_exhausted'
                break
            r, error, elapsed = self.call(self.transport, {'api_name': api, 'token': self.token, 'params': params, 'fields': ''})
            http = r.status_code if r is not None else None
            status, code, msg = error, None, error
            if r is not None:
                if r.is_redirect:
                    status, msg = 'api_error', 'redirect_rejected; credentials_not_forwarded'
                else:
                    try:
                        payload = r.json()
                        code = payload.get('code', payload.get('status_code')) if isinstance(payload, dict) else None
                        msg = payload.get('msg', payload.get('message', payload.get('error'))) if isinstance(payload, dict) else None
                        status = classify_error(http, code, msg)
                        if isinstance(payload, dict) and payload.get('success') is False and not status: status = 'api_error'
                        if not status:
                            fields, rows = parse_rows(payload)
                            preserve_evidence(ROOT,api,params,rows,self.token)
                            result.update(rows=len(rows), fields=fields,
                                          missing_rates={key: {'absent': key not in fields,
                                              'null_count': sum(missing(row.get(key)) for row in rows),
                                              'rate': sum(missing(row.get(key)) for row in rows)/len(rows) if rows else None} for key in KEYS[api]})
                            # Only known public data columns; no arbitrary extra credential fields.
                            result['samples'] = [{k: sanitize(row.get(k), self.token) for k in ['trade_date', 'cal_date']+KEYS[api] if k in row} for row in rows[:5]]
                            for sample in result['samples']:
                                if 'ts_code' in sample: sample['ts_code'] = '[SECURITY_REDACTED]'
                            expected = result['date']
                            date_key = 'cal_date' if api == 'trade_cal' else 'trade_date'
                            mismatches = sum(str(row.get(date_key, '')).replace('-', '') != expected for row in rows)
                            result['date_mismatches'] = mismatches
                            result['at_documented_row_cap'] = len(rows) >= CAPS.get(api, 100000)
                            status = 'success' if rows else 'empty_unverified'
                            if rows and (mismatches or result['at_documented_row_cap']): status = 'quality_unverified'
                    except (ValueError, TypeError, KeyError):
                        status = classify_error(http, None, '') or 'api_error'
                        msg = 'invalid_json_or_unsupported_response_shape'
            result['attempts'].append({'http_code': http, 'server_code': sanitize(code, self.token),
                                       'status': status, 'seconds': elapsed, 'error': sanitize(msg, self.token)})
            result.update(status=status, http_code=http, server_code=sanitize(code, self.token), error=sanitize(msg, self.token))
            if attempt >= self.retries or self.used >= self.max_requests: break
            if status == 'rate_limited':
                print(json.dumps({'event': 'rate_limit_wait', 'seconds': 60, 'api': api}), flush=True)
                time.sleep(60)
            elif (status and status.startswith('network_error')) or (http is not None and http >= 500):
                time.sleep(min(2**attempt, 10))
            else: break
        result['seconds'] = round(sum(a['seconds'] for a in result['attempts']), 3)
        result['backfill'] = '仅建议单月试采并核验，非全量放行' if result['status']=='success' else '不建议，需补充验证'
        print(json.dumps({'api': api, 'date': result['date'], 'status': result['status'], 'rows': result['rows']}, ensure_ascii=False), flush=True)
        return result

    def run(self):
        base, history = cases()
        api, params = base[0]
        authentication = self.query(api, params)
        if authentication['status'] != 'success':
            self.block = 'trade_cal_not_verified; subsequent_authenticated_probes_stopped'
            return
        for api, params in base[1:]: self.query(api, params)
        for api, params in history:
            if self.used >= self.max_requests: break
            if any(r['api']==api and r['status']=='success' for r in self.results): self.query(api, params)


def write_report(probe, target):
    base, _ = cases()
    results = sanitize(probe.results, probe.token)
    out = ['# Tushare 第三方接口探测', '',
           '检查时间：'+datetime.now(ZoneInfo('America/Los_Angeles')).isoformat(), '',
           '仅独立 API 抽样，不连接 PostgreSQL，不全量回填。样例最多 5 条；Token 与敏感字段不记录。', '',
           f'请求计数：{probe.used}/{probe.max_requests}（含无 Token 协议请求及认证重试）；fields 使用空字符串，检查源实际默认字段。', '',
           '## 协议与安全', '', '```json', json.dumps({'checks':probe.network,'blocked':probe.block},ensure_ascii=False,indent=2), '```', '',
           'HTTPS 使用证书链及主机名验证；禁用自动重定向、隐式 netrc 认证及环境代理。HTTP 未明确接受时不发送 Token。', '',
           '## 总结表', '', '| API | 日期/标签 | 状态 | HTTP / 服务码 | 实际条数 | 耗时秒 | 回填建议 |',
           '|---|---|---|---|---:|---:|---|']
    displayed = results or [{'api':api,'params':params,'date':params.get('trade_date',params.get('start_date')),'status':'not_tested','backfill':'阻塞，不能判断权限或覆盖'} for api,params in base]
    for r in displayed:
        label = r['params'].get('limit_type',r['params'].get('tag',''))
        out.append(f"| {r['api']} | {r['date']} {label} | {r['status']} | {r.get('http_code')} / {r.get('server_code')} | {r.get('rows','未请求')} | {r.get('seconds','—')} | {r['backfill']} |")
    for r in results:
        out += ['', '## '+r['api']+' / '+str(r['params']), '', '```json',json.dumps(r,ensure_ascii=False,indent=2),'```']
    ok = sorted({r['api'] for r in results if r['status']=='success'})
    dates = sorted(r['date'] for r in results if r['status']=='success' and r['api']!='trade_cal')
    out += ['', '## 结论', '',
            '- 已证实返回非空且日期匹配的接口：'+(', '.join(ok) if ok else '暂无；未实测成功不等于无权限。'),
            '- 最早实测非空业务日期：'+(dates[0] if dates else '尚无法确定。')+' 抽样最早日期不是供应商历史起始边界。',
            '- 龙头研究：本轮探测范围不足以证明市场全覆盖或标签可靠；认证未完成时不能评估该服务是否满足研究需要。',
            '- 直接入库：本轮没有入库。成功响应可作为后续来源证据；正式事实表仍须验证单位、日期、重复项和交易范围。',
            '- BaoStock/AKShare：保留日线、历史 ST 及近期观测对照，补充专题池不覆盖的股票和待解释差异；不能假定任一源全覆盖。',
            '- 2020 年以来全量回填：本轮不执行，也不凭少量成功响应直接放行；先通过单月试采及缺口审计。', '',
            '## 口径与文档', '',
            '空响应为 empty_unverified，不解释为无权限或市场零事件。零值不是缺失；空表缺失率为 NULL。字段未返回单独标 absent。', '',
            '官方 limit_list_d 文档声明从 2020 年开始且不统计 ST；limit_list_ths 从 2023-11-01 起。第三方是否一致须以实测为准。limit_amount 为板上成交金额，不应当作封单金额；默认输出可能不含全部可选字段。', '',
            'daily_basic 的 total_mv/circ_mv 官方单位为万元，PE 亏损可能为空；第三方单位仍须确认。kpl_list 的封单字段、不同标签下空值需结合源口径核验。limit_cpt_list 是板块统计，不能替代完整历史题材成分。', '']
    out += [f'- [{api} 官方说明](https://tushare.pro/document/2?doc_id={doc})' for api,doc in DOCS.items()]
    target.parent.mkdir(parents=True,exist_ok=True)
    report = '\n'.join(out)+'\n'
    if probe.token: report = report.replace(probe.token, '[REDACTED]')
    target.write_text(report)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--timeout',type=float,default=20)
    p.add_argument('--interval',type=float,default=1)
    p.add_argument('--retries',type=int,default=1)
    p.add_argument('--max-requests',type=int,default=20)
    p.add_argument('--accept-insecure-http',action='store_true')
    a=p.parse_args()
    if not 1<=a.max_requests<=20 or a.timeout<=0 or a.interval<0 or not 0<=a.retries<=2: p.error('invalid bounds')
    env=dotenv_values(ROOT/'.env')
    url=os.environ.get('TUSHARE_API_URL',env.get('TUSHARE_API_URL','')) or ''
    token=os.environ.get('TUSHARE_TOKEN',env.get('TUSHARE_TOKEN','')) or ''
    probe=Probe(url,token,a.timeout,a.interval,a.retries,a.max_requests)
    try:
        if not url: probe.block='TUSHARE_API_URL_missing; no_official_fallback'
        else: probe.preflight(a.accept_insecure_http)
        if probe.transport:
            if not token: probe.block='TUSHARE_TOKEN_missing'
            else: probe.run()
    except KeyboardInterrupt: probe.block='interrupted; partial_results_preserved'
    except Exception as exc: probe.block='unexpected_failure:'+type(exc).__name__
    finally:
        write_report(probe,ROOT/'docs/tushare_probe.md')
        probe.session.close()
    print(json.dumps({'requests':probe.used,'blocked':probe.block,'report':'docs/tushare_probe.md'},ensure_ascii=False))

if __name__=='__main__':main()
