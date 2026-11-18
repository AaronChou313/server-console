#!/usr/bin/env python3
"""Small local broker for a dedicated Mihomo. Standard library only, no shell.

Installed as a root-owned copy for TUN. The HTTP socket belongs to one OS user.
Never controls another proxy client. No host routing is changed during staging.
"""
import argparse
import copy
from contextlib import nullcontext
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import signal
import socketserver
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

LIMIT = 4 * 1024 * 1024
EXCLUDES = ['127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10']


def atomic(path, value):
    fd, name = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, ensure_ascii=False); f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def normalize(source, home, secret, enabled=False, mode=None, *, system_tun=False, mixed_port=17890, controller_port=19090):
    """Own listeners/routes; retain user rules, DNS and outbound configuration."""
    allowed = {'proxies', 'proxy-groups', 'proxy-providers', 'rules', 'rule-providers', 'dns', 'hosts',
               'sniffer', 'mode', 'log-level', 'ipv6', 'find-process-mode', 'unified-delay',
               'tcp-concurrent', 'global-client-fingerprint', 'global-ua', 'geodata-mode',
               'geodata-loader', 'geox-url', 'geo-auto-update', 'geo-update-interval',
               'keep-alive-interval', 'keep-alive-idle', 'disable-keep-alive'}
    if any(str(key).startswith('x-clash-') for key in source):
        raise ValueError('此配置是客户端内部格式，请导入机场提供的标准 Clash / Mihomo YAML')
    cfg = {k: copy.deepcopy(v) for k, v in source.items() if k in allowed}
    if not isinstance(cfg.get('proxies', []), list) or not isinstance(cfg.get('proxy-groups', []), list):
        raise ValueError('proxies 和 proxy-groups 必须为列表')
    for key in ('proxy-providers', 'rule-providers'):
        providers = cfg.get(key, {})
        if not isinstance(providers, dict): raise ValueError('Provider 格式无效')
        for name, row in providers.items():
            if not isinstance(row, dict) or row.get('type') not in ('http', 'inline'):
                raise ValueError('Provider 请使用 http 或 inline；本地文件请先合并进配置')
            if row.get('type') == 'http':
                parsed = urlsplit(str(row.get('url', '')))
                if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError('Provider URL 无效')
            row['path'] = str(home / 'providers' / (hashlib.sha256((key+name).encode()).hexdigest()+'.yaml'))
    cfg.update({'mixed-port':mixed_port, 'port':0, 'socks-port':0, 'redir-port':0, 'tproxy-port':0,
                'allow-lan':False, 'bind-address':'127.0.0.1', 'external-controller':f'127.0.0.1:{controller_port}',
                'secret':secret, 'profile':{'store-selected':True, 'store-fake-ip':True}})
    cfg['mode'] = mode or cfg.get('mode', 'rule')
    if cfg['mode'] not in ('rule', 'global', 'direct'): raise ValueError('模式无效')
    cfg.setdefault('log-level', 'info')
    rules = cfg.setdefault('rules', ['MATCH,DIRECT'])
    if not isinstance(rules, list) or not all(isinstance(x, str) for x in rules): raise ValueError('rules 必须为字符串列表')
    if system_tun:
        cfg['rules'] = ['IP-CIDR,'+x+',DIRECT,no-resolve' for x in EXCLUDES] + rules
    default_dns = {'enable': True, 'enhanced-mode': 'fake-ip',
                   'nameserver': ['https://dns.alidns.com/dns-query', 'https://1.1.1.1/dns-query'],
                   'default-nameserver': ['223.5.5.5', '1.1.1.1']} if system_tun else {}
    dns = cfg.setdefault('dns', default_dns)
    if not isinstance(dns, dict): raise ValueError('DNS 配置无效')
    if system_tun:
        dns.update(enable=True, listen='127.0.0.1:1054')
    else:
        dns.pop('listen', None)
    cfg['tun'] = {'enable': enabled and system_tun, 'stack':'mixed', 'dns-mode':'native',
                  'auto-route':system_tun, 'auto-detect-interface':system_tun,
                  'dns-hijack':['any:53'] if system_tun else [],
                  'route-exclude-address':EXCLUDES, 'strict-route':False}
    return cfg


def clash_connected():
    if __import__('sys').platform != 'darwin': return False
    try:
        value = subprocess.run(['/usr/sbin/scutil', '--nc', 'list'], capture_output=True, text=True, timeout=4, check=True)
    except (OSError, subprocess.SubprocessError):
        raise ValueError('无法确认 Clash 连接状态，未启用新代理')
    return any('[VPN:com.hako.network]' in line and '(Disconnected)' not in line for line in value.stdout.splitlines())


class Engine:
    def __init__(self, home, binary, system_tun, mixed_port, controller_port, validate_only=False):
        self.home, self.binary = home, binary
        self.system_tun, self.mixed_port, self.controller_port = system_tun, mixed_port, controller_port
        self.lock = threading.RLock()
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        (home/'providers').mkdir(mode=0o700, exist_ok=True)
        self.secret_path = home/'secret.json'
        if not self.secret_path.exists(): atomic(self.secret_path, __import__('secrets').token_urlsafe(36))
        self.secret = json.loads(self.secret_path.read_text())
        self.state_path = home/'state.json'
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {'source':{}, 'enabled':False, 'mode':'rule'}
        # A second client may have been enabled while this service was down.
        self.process = None
        if validate_only:
            self.validate(self.state['source'])
        else:
            if self.system_tun and self.state['enabled'] and clash_connected():
                raise ValueError('Clash 仍在连接，请先关闭后再启动 Mihomo')
            self.write_config()
            if self.desired_running():
                self.launch()

    def core(self, method, path, data=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.controller_port, timeout=30)
        try:
            connection.request(method, path, body=json.dumps(data).encode() if data is not None else None,
                               headers={'Authorization':'Bearer '+self.secret, 'Content-Type':'application/json'})
            result = connection.getresponse(); raw = result.read(8*1024*1024+1)
            if len(raw)>8*1024*1024: raise ValueError('内核响应过大')
            if result.status>=400: raise ValueError('内核拒绝操作，HTTP '+str(result.status))
            return json.loads(raw) if raw else {}
        finally: connection.close()

    def config(self, source=None, enabled=None):
        return normalize(self.state['source'] if source is None else source, self.home, self.secret,
                         self.state['enabled'] if enabled is None else enabled, self.state.get('mode'),
                         system_tun=self.system_tun, mixed_port=self.mixed_port, controller_port=self.controller_port)

    def write_config(self):
        atomic(self.home/'config.json', self.config())
        atomic(self.state_path, self.state)

    def launch(self):
        # Do not replace an unknown controller or kill any unrelated process.
        import socket
        with socket.socket() as s:
            if s.connect_ex(('127.0.0.1',self.controller_port))==0: raise ValueError('本机控制端口已被占用')
        logfile=self.home/'engine.log'
        if logfile.exists() and logfile.stat().st_size>8*1024*1024: logfile.replace(self.home/'engine.previous.log')
        with logfile.open('ab') as log:
            self.process=subprocess.Popen([str(self.binary), '-d', str(self.home), '-f', str(self.home/'config.json')],
                                          stdout=log, stderr=log, stdin=subprocess.DEVNULL, env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin', 'HOME':str(self.home)})
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            if self.process.poll() is not None: raise ValueError('Mihomo 启动失败，请查看内核日志')
            try: self.core('GET','/version'); return
            except (OSError, ValueError): time.sleep(.1)
        self.stop()
        raise ValueError('Mihomo 启动超时')

    def desired_running(self):
        return self.system_tun or self.state['enabled']

    def running(self):
        return self.process is not None and self.process.poll() is None

    def status(self):
        return dict(ready=self.running(), enabled=self.state['enabled'], tun=self.system_tun,
                    configured=bool(self.state['source']), revision=digest(self.state),
                    config_revision=digest(self.state['source']), mixed_port=self.mixed_port,
                    pid=self.process.pid if self.running() else None)

    def validate(self, source):
        if not isinstance(source,dict): raise ValueError('配置格式无效')
        cfg=self.config(source=source,enabled=False)
        atomic(self.home/'candidate.json',cfg)
        try:
            result=subprocess.run([str(self.binary),'-t','-d',str(self.home),'-f',str(self.home/'candidate.json')],
                                  stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=35)
            if result.returncode: raise ValueError('Mihomo 配置检查失败，原配置保持不变。请检查 YAML、节点协议或规则依赖')
        except subprocess.TimeoutExpired:
            raise ValueError('Mihomo 校验超时，请检查 GeoIP 数据和远程规则的下载地址') from None
        finally:
            (self.home/'candidate.json').unlink(missing_ok=True)
        return {'ok':True}

    def change(self, body):
        if body.get('revision') != digest(self.state):
            raise ValueError('状态已变化，请刷新后重试')
        action = body.get('action')
        previous = copy.deepcopy(self.state)
        if action == 'enable':
            if self.system_tun and (not self.state['source'] or clash_connected()):
                raise ValueError('启用 TUN 需要代理配置，且 Clash 必须断开')
            self.state['enabled'] = True
        elif action == 'disable':
            self.state['enabled'] = False
        elif action == 'apply':
            if not isinstance(body.get('source'), dict):
                raise ValueError('配置格式无效')
            self.state['source'] = body['source']
        elif action != 'restart':
            raise ValueError('不支持的内核操作')

        runtime_changed = False
        try:
            if action != 'disable':
                self.validate(self.state['source'])
            runtime_changed = True
            self.apply_runtime(restart=action == 'restart')
            self.write_config()
        except Exception:
            self.state = previous
            if runtime_changed:
                try:
                    self.apply_runtime()
                    self.write_config()
                except (OSError, ValueError, subprocess.SubprocessError):
                    pass
            raise
        return self.status()

    def apply_runtime(self, restart=False):
        if restart or not self.desired_running():
            self.stop()
        if not self.desired_running():
            return
        if not self.running():
            self.write_config()
            self.launch()
        else:
            self.core('PUT', '/configs?force=true', {'payload': json.dumps(self.config())})
        actual = self.core('GET', '/configs').get('tun', {}).get('enable', False)
        if bool(actual) != (self.system_tun and self.state['enabled']):
            raise ValueError('TUN 状态与目标不同')

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=8)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait()


class Server(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
    allow_reuse_address=True
    daemon_threads=True
    last_retry=0

    def service_actions(self):
        if not self.engine.desired_running() or self.engine.running() or time.monotonic()-self.last_retry<30:return
        if not self.engine.lock.acquire(blocking=False):return
        self.last_retry=time.monotonic()
        try:
            if self.engine.system_tun and self.engine.state['enabled'] and clash_connected():return
            self.engine.write_config();self.engine.launch()
        except (ValueError,OSError,subprocess.SubprocessError):pass
        finally:self.engine.lock.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass  # subscription/config secrets never reach request logs

    def do_GET(self): self.handle_request()
    def do_POST(self): self.handle_request()
    def do_PUT(self): self.handle_request()
    def do_PATCH(self): self.handle_request()
    def do_DELETE(self): self.handle_request()

    def handle_request(self):
        status=200
        try:
            engine=self.server.engine
            size=int(self.headers.get('Content-Length','0'))
            if size<0 or size>LIMIT: raise ValueError('请求过大')
            body=json.loads(self.rfile.read(size)) if size else None
            readonly_core=self.command=='GET' and (self.path in ('/version','/configs','/proxies','/connections','/rules','/providers/proxies','/providers/rules') or re.fullmatch(r'/proxies/[^/]+(?:/delay\?[^\r\n]+)?',self.path))
            with nullcontext() if readonly_core else engine.lock:
                if self.path=='/engine/state' and self.command=='GET': result=engine.status()
                elif self.path=='/engine/control' and self.command=='POST': result=engine.change(body or {})
                elif self.path=='/engine/validate' and self.command=='POST': result=engine.validate((body or {}).get('source'))
                elif self.path=='/engine/logs' and self.command=='GET':
                    try:
                        with (engine.home / 'engine.log').open('rb') as log:
                            log.seek(max(0, os.fstat(log.fileno()).st_size - 32768))
                            value = log.read().decode(errors='replace')
                    except FileNotFoundError:
                        value = ''
                    # Do not publish URLs (may include subscription tokens) or UUIDs.
                    value=re.sub(r'https?://[^\s"\x1b]+','[URL]',value)
                    value=re.sub(r'\b[\da-fA-F]{8}(?:-[\da-fA-F]{4}){3}-[\da-fA-F]{12}\b','[credential]',value)
                    result={'text':value}
                elif self.command=='GET' and (self.path in ('/version','/configs','/proxies','/connections','/rules','/providers/proxies','/providers/rules') or re.fullmatch(r'/proxies/[^/]+(?:/delay\?[^\r\n]+)?',self.path)):
                    result=engine.core(self.command,self.path)
                elif self.command=='PATCH' and self.path=='/configs' and isinstance(body,dict) and set(body)=={'mode'} and body['mode'] in ('rule','global','direct'):
                    result=engine.core('PATCH','/configs',body); engine.state['mode']=body['mode']; engine.write_config()
                elif self.command=='PUT' and re.fullmatch(r'/proxies/[^/]+',self.path) and isinstance(body,dict) and set(body)=={'name'}:
                    result=engine.core('PUT',self.path,body)
                elif self.command=='PUT' and re.fullmatch(r'/providers/proxies/[^/]+',self.path):
                    result=engine.core(self.command,self.path)
                elif self.command=='GET' and re.fullmatch(r'/providers/proxies/[^/]+/healthcheck',self.path):
                    result=engine.core(self.command,self.path)
                elif self.command=='DELETE' and (re.fullmatch(r'/connections(?:/[A-Za-z0-9-]+)?',self.path) or re.fullmatch(r'/proxies/[^/]+',self.path)): result=engine.core('DELETE',self.path)
                else: raise ValueError('此接口未开放')
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            status = 409
            result = {'detail': str(error) if isinstance(error, ValueError) else '内核不可用，请查看日志后重试'}
        raw=json.dumps(result,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home',type=Path,required=True)
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--socket',type=Path,required=True)
    parser.add_argument('--uid',type=int,required=True)
    parser.add_argument('--system-tun',action='store_true')
    parser.add_argument('--mixed-port',type=int,default=17890)
    parser.add_argument('--controller-port',type=int,default=19090)
    parser.add_argument('--validate-only',action='store_true')
    args=parser.parse_args()
    if args.system_tun and (__import__("sys").platform != "darwin" or os.geteuid() != 0):
        parser.error("系统 TUN 需要 macOS 管理员")
    if args.validate_only:
        Engine(args.home,args.binary,args.system_tun,args.mixed_port,args.controller_port,True)
        return
    if args.socket.exists():
        import socket
        with socket.socket(socket.AF_UNIX) as s:
            if s.connect_ex(str(args.socket))==0: raise SystemExit('代理服务已经运行')
        args.socket.unlink()
    engine=Engine(args.home,args.binary,args.system_tun,args.mixed_port,args.controller_port)
    try:
        with Server(str(args.socket),Handler) as server:
            server.engine=engine
            os.chmod(args.socket,0o600)
            if os.geteuid()==0: os.chown(args.socket,args.uid,-1)
            def shutdown(*_): raise KeyboardInterrupt()
            signal.signal(signal.SIGTERM,shutdown); signal.signal(signal.SIGINT,shutdown)
            try: server.serve_forever(poll_interval=.5)
            except KeyboardInterrupt: pass
    finally:
        engine.stop(); args.socket.unlink(missing_ok=True)


if __name__=='__main__': main()
