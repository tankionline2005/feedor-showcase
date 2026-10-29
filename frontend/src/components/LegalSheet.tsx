import { useEffect, useState } from 'react'
import { LEGAL, type LegalDoc } from '../lib/legal'
import { Sheet } from './Sheet'

/** «Пользовательское соглашение» or «Политика конфиденциальности», read on the site. */
export function LegalSheet({ doc, onClose }: { doc: LegalDoc | null; onClose: () => void }) {
  // the last one shown stays while the sheet leaves
  const [shown, setShown] = useState<LegalDoc | null>(doc)
  useEffect(() => {
    if (doc) setShown(doc)
  }, [doc])
  const text = shown ? LEGAL[shown] : null
  return (
    <Sheet
      open={doc !== null}
      onClose={onClose}
      title={text?.title ?? ''}
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} onClick={onClose}>
          Готово
        </button>
      }
    >
      {text && (
        <article className="legal">
          <p className="legal-updated">Редакция от {text.updated}</p>
          <p>{text.intro}</p>
          {text.sections.map((section) => (
            <section key={section.title}>
              <h3>{section.title}</h3>
              {section.paragraphs.map((p) =>
                p.startsWith('— ') ? (
                  <p key={p} className="legal-item">
                    {p.slice(2)}
                  </p>
                ) : (
                  <p key={p}>{p}</p>
                ),
              )}
            </section>
          ))}
        </article>
      )}
    </Sheet>
  )
}

/** The two documents' links, for a footer: «Соглашение · Конфиденциальность». */
export function LegalLinks({ onOpen }: { onOpen: (doc: LegalDoc) => void }) {
  return (
    <>
      <button onClick={() => onOpen('terms')}>Соглашение</button>
      <button onClick={() => onOpen('privacy')}>Конфиденциальность</button>
    </>
  )
}
