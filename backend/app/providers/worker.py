"""Subprocess boundary bounds third-party calls that do not expose a timeout."""
import contextlib
import inspect
import json
import sys


def main():
    request = json.load(sys.stdin)
    provider, endpoint, params = request['provider'], request['endpoint'], request['params']
    with contextlib.redirect_stdout(sys.stderr):
        if provider == 'akshare':
            import akshare as ak
            fn = getattr(ak, endpoint)
            signature = str(inspect.signature(fn))
            df = fn(**params)
            version = ak.__version__
        elif provider == 'baostock':
            from app.providers.baostock import fetch
            payload = fetch(endpoint, params)
            with contextlib.redirect_stdout(sys.__stdout__):
                print(json.dumps(payload, ensure_ascii=False))
            return
        elif provider == 'tushare':
            from app.providers.tushare_proxy import ProxyClient
            from app.core.config import settings
            client = ProxyClient(accept_http=settings.tushare_allow_http, max_requests=10)
            try:
                payload = client.fetch(endpoint, **params)
            finally:
                client.session.close()
            with contextlib.redirect_stdout(sys.__stdout__):
                print(json.dumps(payload, ensure_ascii=False))
            return
        else:
            raise ValueError('Unsupported provider')
    print(json.dumps({'version':version,'signature':signature,'columns':list(df.columns),
                      'records':json.loads(df.to_json(orient='records',date_format='iso',force_ascii=False))}, ensure_ascii=False))

if __name__ == '__main__': main()
