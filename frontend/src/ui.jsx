import React, {useEffect, useId, useRef} from 'react'
import {AlertCircle, LoaderCircle, X} from 'lucide-react'

export function Button({icon: Icon, variant = '', className = '', children, ...props}) {
  return <button type="button" className={['button', variant, children ? '' : 'icon-button', className].join(' ')} {...props}>
    {Icon && <Icon size={16} strokeWidth={1.8}/>} {children}
  </button>
}

export function Badge({tone = 'neutral', children}) {
  return <span className={'badge ' + tone}><i/>{children}</span>
}

export function PageTitle({icon: Icon, title, children}) {
  return <div className="page-title">
    <div>{Icon && <span className="page-icon"><Icon size={21}/></span>}<h1>{title}</h1></div>
    {children && <div className="actions">{children}</div>}
  </div>
}

export function ErrorNotice({children}) {
  return children ? <div role="alert" className="notice error"><AlertCircle size={17}/><span>{children}</span></div> : null
}

export function Loading({children = '正在读取主机数据…'}) {
  return <div className="empty" role="status"><LoaderCircle className="spinner" size={20}/> {children}</div>
}

export function Empty({icon: Icon, title, children}) {
  return <div className="empty">{Icon && <span className="page-icon"><Icon size={23}/></span>}<strong>{title}</strong>{children}</div>
}

export function Modal({title, onClose, children}) {
  const ref = useRef(null)
  const titleId = useId()
  useEffect(() => {
    const el = ref.current
    el.showModal()
    return () => el.close()
  }, [])
  return <dialog ref={ref} className="modal" aria-labelledby={titleId} onCancel={event => {event.preventDefault(); onClose()}}>
    <header><h2 id={titleId}>{title}</h2><Button icon={X} aria-label="关闭" onClick={onClose}/></header>
    <div className="modal-content">{children}</div>
  </dialog>
}

export function CardTitle({icon: Icon, title, children}) {
  return <div className="card-title"><h2>{Icon && <Icon size={17}/>} {title}</h2>{children}</div>
}
