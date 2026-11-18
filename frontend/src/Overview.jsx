import React, {useEffect, useId, useState} from 'react'
import {Activity, ArrowDown, ArrowDownUp, ArrowRight, Clock3, Cpu, HardDrive, MemoryStick, Monitor, Network, SquareTerminal, Box, KeyRound, Sparkles} from 'lucide-react'
import {bytes, uptime} from './api'
import {Badge, CardTitle, ErrorNotice, PageTitle} from './ui'

function chartPoints(values, max, width, height) {
  return values.map((value, index) => [
    width - (values.length - 1 - index) * width / 39,
    height * (1 - Math.max(0, Math.min(1, value / max))),
  ]).map(point => point.map(n => n.toFixed(1)).join(','))
}

function Sparkline({values, max = 100, color = 'blue'}) {
  const points = chartPoints(values, max, 100, 31)
  return <svg className={'sparkline ' + color} viewBox="0 0 100 38" aria-hidden="true">
    {points.length > 1 && <><polygon points={points[0].split(',')[0] + ',35 ' + points.join(' ') + ' 100,35'}/><polyline points={points.join(' ')}/></>}
  </svg>
}

function Metric({icon: Icon, name, value, detail, values, color, max, percent}) {
  return <article className="card metric">
    <div className="metric-label"><span className={'metric-icon ' + color}><Icon size={17}/></span>{name}</div>
    <div className="metric-value">{value}</div>
    <div className="metric-foot"><span>{detail}</span>{values && <Sparkline values={values} color={color} max={max}/>}</div>
    {percent !== undefined && <div className="progress"><i style={{width: percent + '%'}}/></div>}
  </article>
}

const shortcuts = [
  [SquareTerminal, '终端', '连接 Zellij 会话', 'terminal'],
  [Box, 'Docker', '查看容器与日志', 'docker'],
  [KeyRound, 'SSH', '连接已配置的主机', 'ssh'],
  [Sparkles, 'AI 工作台', '打开 Codex 工作区', 'codex'],
]

export default function Overview({data, error, go}) {
  const [samples, setSamples] = useState([])
  const [metric, setMetric] = useState('usage')
  const gradient = useId()
  useEffect(() => {
    if (data) setSamples(items => [...items.slice(-39), data])
  }, [data])
  const cpu = samples.map(s => s.cpu), memory = samples.map(s => s.memory.percent)
  const down = samples.map(s => s.net.down), up = samples.map(s => s.net.up)
  const netMax = Math.max(1024, ...down, ...up)
  const lines = metric === 'usage' ? [cpu, memory] : [down, up]
  const labels = metric === 'usage' ? ['CPU', '内存'] : ['接收', '发送']
  const max = metric === 'usage' ? 100 : netMax
  const series = lines.map(values => chartPoints(values, max, 640, 180))
  return <div className="page overview">
    <PageTitle title="系统概览" description="监控主机资源、运行状态与常用服务。">
      <Badge tone={error ? 'bad' : data ? 'good' : 'neutral'}>{error ? '采样中断' : data ? '实时更新 · 3 秒' : '正在连接'}</Badge>
    </PageTitle>
    <ErrorNotice>{error}</ErrorNotice>
    <section className="metrics" aria-label="实时系统指标">
      <Metric icon={Cpu} name="CPU 使用率" value={data ? data.cpu.toFixed(1) + '%' : '—'} detail={data ? data.cores + ' 逻辑核心' : '等待采样'} values={cpu} color="blue"/>
      <Metric icon={MemoryStick} name="内存使用率" value={data ? data.memory.percent.toFixed(1) + '%' : '—'} detail={data ? bytes(data.memory.used) + ' / ' + bytes(data.memory.total) : '等待采样'} values={memory} color="blue"/>
      <Metric icon={HardDrive} name="磁盘使用率" value={data ? data.disk.percent.toFixed(1) + '%' : '—'} detail={data ? bytes(data.disk.used) + ' / ' + bytes(data.disk.total) : '等待采样'} percent={data?.disk.percent ?? 0} color="slate"/>
      <Metric icon={ArrowDownUp} name="网络流量" value={data ? <><ArrowDown size={20}/>{bytes(data.net.down)}<small>/s</small></> : '—'} detail={data ? '发送 ' + bytes(data.net.up) + '/s' : '等待采样'} values={down} color="blue" max={netMax}/>
    </section>
    <div className="overview-grid">
      <section className="card chart-panel">
        <CardTitle icon={Activity} title="资源趋势">
          <div className="segments"><button className={metric === 'usage' ? 'selected' : ''} onClick={() => setMetric('usage')}>CPU / 内存</button><button className={metric === 'network' ? 'selected' : ''} onClick={() => setMetric('network')}>网络</button></div>
        </CardTitle>
        <div className="legend"><span><i/>{labels[0]}</span><span><i/>{labels[1]}</span></div>
        <div className="chart">
          <div className="axis-y">{[1, .75, .5, .25, 0].map(n => <span key={n}>{metric === 'usage' ? n * 100 + '%' : bytes(n * max, 0) + '/s'}</span>)}</div>
          <div className="plot">
            <svg viewBox="0 0 640 180" preserveAspectRatio="none" role="img" aria-label={'最近两分钟的' + labels.join('和') + '趋势'}>
              <defs><linearGradient id={gradient} x2="0" y2="1" gradientUnits="objectBoundingBox"><stop stopColor="#4d7cff" stopOpacity=".18"/><stop offset="1" stopColor="#4d7cff" stopOpacity="0"/></linearGradient></defs>
              {[0, 45, 90, 135, 180].map(y => <line key={y} x1="0" y1={y} x2="640" y2={y} className="grid-line"/>)}
              {series[0].length > 1 && <polygon fill={`url(#${gradient})`} points={series[0][0].split(',')[0] + ',180 ' + series[0].join(' ') + ' 640,180'}/>}
              {series.map((points, i) => <polyline key={i} points={points.join(' ')} className={'series s' + i} vectorEffect="non-scaling-stroke"/>)}
            </svg>
            {samples.length < 2 && <span className="chart-wait">正在采样…</span>}
          </div>
          <div className="axis-x"><span>2 分钟前</span><span>1 分钟前</span><span>现在</span></div>
        </div>
      </section>
      <section className="card host-panel">
        <CardTitle icon={Monitor} title="主机详情"/>
        <div className="host-identity"><span className="host-icon"><Monitor size={25}/></span><div><strong>{data?.hostname || '当前主机'}</strong><p>{data?.os || '读取系统信息'}</p></div></div>
        <dl className="details">
          <div><dt><Cpu size={15}/>架构</dt><dd>{data?.arch || '—'}</dd></div>
          <div><dt><Network size={15}/>主机 IPv4</dt><dd>{data?.ip || '—'}</dd></div>
          <div><dt><Clock3 size={15}/>运行时间</dt><dd>{uptime(data?.uptime)}</dd></div>
        </dl>
        <button className="text-link" onClick={() => go('network')}>查看网络状态 <ArrowRight size={15}/></button>
      </section>
    </div>
    <section className="quick-section"><h2>快捷操作</h2><div className="shortcuts">{shortcuts.map(([Icon, title, description, page]) =>
      <button className="card shortcut" key={page} onClick={() => go(page)}><span className="metric-icon slate"><Icon size={17}/></span><span><strong>{title}</strong><small>{description}</small></span><ArrowRight className="shortcut-arrow" size={16}/></button>
    )}</div></section>
  </div>
}
