// Fixed cover letter for English and Slovak prose (ADR-009).
// The model already contains the wording. This file only places it.
// Single column, no tables, frames, images, or file reads.

#let data = json(sys.inputs.data)
#let lang = sys.inputs.lang

#set page(paper: "a4", margin: (x: 1.8cm, y: 1.6cm))
#set text(font: ("Liberation Sans", "Roboto"), size: 11pt, lang: lang)
#set par(justify: false, leading: 0.7em)

#let filled(value) = value != none and value != ""

#text(weight: "bold")[#data.contact.name]
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
}

#if filled(data.date) [
  #v(0.9em)
  #data.date
]

#v(0.9em)
#{
  let recipient = data.at("recipient", default: (:))
  let name = recipient.at("name", default: none)
  let company = recipient.at("company", default: none)
  let address = recipient.at("address", default: none)
  if filled(name) {
    name
    linebreak()
  }
  if filled(company) {
    company
    linebreak()
  }
  if filled(address) {
    address
    linebreak()
  }
}

#if filled(data.role) [
  #v(0.7em)
  #text(weight: "bold")[#data.role]
]

#v(0.8em)
#data.salutation

#v(0.55em)
#for paragraph in data.paragraphs [
  #paragraph
  #v(0.55em)
]

#data.closing
#v(0.9em)
#data.signature
