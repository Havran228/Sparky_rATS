// Fixed English resume. Data comes from sys.inputs inside the compile root (ADR-009).
// Single column, no tables, frames, images, or file reads.

#let data = json(sys.inputs.data)
#let lang = sys.inputs.lang

#set page(paper: "a4", margin: (x: 1.8cm, y: 1.6cm))
#set text(font: ("Liberation Sans", "Roboto"), size: 10.5pt, lang: lang)
#set par(justify: false, leading: 0.65em)

#let filled(value) = value != none and value != ""

#text(size: 16pt, weight: "bold")[#data.contact.name]
#linebreak()
#{
  let contact = data.contact
  let bits = (
    contact.at("email", default: none),
    contact.at("phone", default: none),
    contact.at("location", default: none),
  ).filter(filled)
  if bits.len() > 0 {
    bits.join("  ·  ")
    linebreak()
  }
  let links = contact.at("links", default: ()).filter(filled)
  if links.len() > 0 {
    links.join("  ·  ")
    linebreak()
  }
}

#if filled(data.summary) [
  #v(0.85em)
  #text(size: 12pt, weight: "bold")[Summary]
  #v(0.25em)
  #data.summary
]

#if data.experience.len() > 0 [
  #v(0.85em)
  #text(size: 12pt, weight: "bold")[Experience]
  #v(0.35em)
  #for job in data.experience [
    #text(weight: "bold")[#job.title] — #job.company
    #{
      let start = job.at("start", default: none)
      let finish = job.at("end", default: none)
      let dates = if filled(start) and filled(finish) {
        start + " – " + finish
      } else if filled(start) {
        start
      } else if filled(finish) {
        finish
      } else {
        none
      }
      if dates != none {
        h(0.5em)
        dates
      }
      if filled(job.location) {
        h(0.5em)
        job.location
      }
    }
    #if job.bullets.len() > 0 [
      #list(..job.bullets)
    ]
    #v(0.4em)
  ]
]

#if data.skills.len() > 0 [
  #v(0.45em)
  #text(size: 12pt, weight: "bold")[Skills]
  #v(0.25em)
  #for group in data.skills [
    #text(weight: "bold")[#group.name:] #group.items.join(", ")
    #linebreak()
  ]
]

#if data.education.len() > 0 [
  #v(0.45em)
  #text(size: 12pt, weight: "bold")[Education]
  #v(0.25em)
  #for item in data.education [
    #text(weight: "bold")[#item.institution]
    #if filled(item.credential) [ — #item.credential]
    #if filled(item.detail) [
      #linebreak()
      #item.detail
    ]
    #v(0.3em)
  ]
]

#if data.languages.len() > 0 [
  #v(0.45em)
  #text(size: 12pt, weight: "bold")[Languages]
  #v(0.25em)
  #data.languages.map(item => {
    if filled(item.level) { item.language + " (" + item.level + ")" } else { item.language }
  }).join(", ")
]
