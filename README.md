# Digi (RCS & RDS) — Integrare Home Assistant

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.12%2B-41BDF5?logo=homeassistant&logoColor=white)](https://www.home-assistant.io/)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/coffeerunhobby/ha-digi-rds-rcs)](https://github.com/coffeerunhobby/ha-digi-rds-rcs/releases)
[![GitHub Stars](https://img.shields.io/github/stars/coffeerunhobby/ha-digi-rds-rcs?style=flat&logo=github)](https://github.com/coffeerunhobby/ha-digi-rds-rcs/stargazers)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Integrare neoficială Home Assistant pentru serviciile **Digi România** (fostul
**RCS & RDS**). Permite autentificarea în contul tău Digi și expune facturile,
sumele de plată și serviciile active sub formă de entități și senzori în Home
Assistant.

> ⚠️ Acesta este un proiect dezvoltat de comunitate și nu este afiliat, susținut
> sau aprobat de Digi România. Integrarea funcționează prin accesarea
> informațiilor disponibile în contul tău Digi (nu există un API public) și
> poate necesita actualizări dacă platforma Digi se modifică.

---

## Funcționalități

- Autentificare cu e-mail și parolă.
- Suport pentru autentificare în doi pași (SMS sau e-mail).
- Suport pentru conturi cu mai multe adrese.
- Suport pentru mai multe conturi Digi, fiecare cu propria sesiune (cookie).
- Conturile sunt actualizate **pe rând** (round-robin), nu simultan — câte o
  reîmprospătare la fiecare interval, ciclând prin conturi.
- Dispozitiv separat pentru fiecare adresă monitorizată.
- Senzori per adresă: suma de plată, valoarea ultimei facturi, data scadenței,
  existența restanțelor și numărul serviciilor facturate.
- Senzor opțional pentru IP-ul public al serviciului de internet (dezactivat
  implicit).
- Senzori opționali de conexiune FiberLink (dezactivați implicit): stare/uptime
  best-effort, plus trafic descărcat/încărcat ca statistici native HA (grafice
  lunare), cu istoric păstrat ~6 luni.
- Istoric facturi și detalii complete disponibile ca atribute.
- Interval de actualizare și număr de facturi citite configurabile.
- Reautentificare automată atunci când sesiunea expiră.

---

## Instalare

### Prin HACS (recomandat)

1. Deschide **HACS** în Home Assistant.
2. Click pe cele trei puncte (⋮) din colțul dreapta sus → **Custom repositories**.
3. Adaugă URL-ul `https://github.com/coffeerunhobby/ha-digi-rds-rcs`, alege
   categoria **Integration**, apoi click **Add**.
4. Caută **Digi (RCS & RDS)** → **Download**.
5. Repornește Home Assistant.

### Manual

1. Copiază directorul `custom_components/digi` în `config/custom_components`.
2. Repornește Home Assistant.

---

## Configurare

1. Accesează **Settings → Devices & Services → Add Integration**.
2. Caută integrarea **Digi**.
3. Introdu **e-mailul** și **parola** contului Digi, intervalul de actualizare
   și numărul de facturi citite per adresă.
4. Dacă este activă autentificarea în doi pași, alege metoda (SMS sau e-mail) și
   introdu codul primit.

**Toate adresele** asociate contului sunt descoperite automat și apar ca
dispozitive separate — nu trebuie să le adaugi manual.

Intervalul de actualizare și numărul de facturi citite pot fi modificate
ulterior din **Configure** (⚙️), fără a reinstala integrarea.

> Pentru un alt cont Digi, folosește **Add service** și autentifică-te cu
> celălalt e-mail — fiecare cont primește propria sesiune și propriile adrese.

---

## Entități disponibile

Fiecare cont Digi este o **intrare** (denumită după e-mail), iar fiecare
**adresă este un dispozitiv** separat (denumit după adresă), cu propriii
senzori.

### Per adresă (dispozitiv)

| Senzor | Descriere |
| --- | --- |
| Amount due | Suma de plată pentru adresa respectivă (RON) |
| Last invoice | Valoarea celei mai recente facturi (RON) |
| Due date | Scadența ultimei facturi |
| Overdue | `yes` / `no` — dacă există sold neachitat |
| Number of services | Numărul serviciilor facturate |
| Public IP | IPv4 public al serviciului de internet (doar la adresele cu internet; dezactivat implicit); IPv6 și planul ca atribute |
| Connection status | Stare conexiune (best-effort) + `connected_since`, ultima conectare/deconectare, reconectări (30z), IP/MAC curent și ultimele sesiuni ca atribute |
| Connection uptime | Momentul (timestamp) când a început sesiunea curentă — afișat ca „acum X zile” |

Senzorul *Amount due* include atribute detaliate: serviciile facturate,
numărul facturii, datele de emitere și scadență, statusul, valoarea facturii,
linkul către PDF, defalcarea pe servicii și istoricul complet al facturilor.

> 🌐 Senzorii de conexiune (status / uptime) apar **doar la adresele cu
> FiberLink** și sunt **dezactivați implicit**. Digi expune doar sesiunile
> încheiate, fără un indicator „online acum”, așa că starea este *best-effort*:
> se presupune că linia s-a reconectat automat la ultima deconectare.

### Trafic (grafice native)

Traficul descărcat/încărcat este expus ca **statistici pe termen lung** ale Home
Assistant (nu ca entități), astfel încât poate fi afișat direct cu cardul
**Statistics Graph** — pe oră / zi / săptămână / lună — fără carduri suplimentare.
Fiecare actualizare interoghează doar ultimele 30 de zile, dar sesiunile sunt
păstrate local ~6 luni și re-injectate ca statistici, deci graficele lunare
acoperă mai multe luni fără a solicita repetat site-ul. Traficul unei sesiuni
care se întinde pe mai multe luni este distribuit proporțional pe zile, iar
indexarea se face pe **adresă** (nu pe IP, care se schimbă la PPPoE).

> Statisticile se numesc `digi:connection_download_<id>` și
> `digi:connection_upload_<id>` — caută-le în cardul *Statistics Graph*.

> ℹ️ `entity_id`-ul folosește **codul de client** și **id-ul de adresă Digi**
> (de ex. `sensor.digi_123456_11112222_amount_due`). Dacă id-ul de adresă nu
> este disponibil, se folosește un hash (md5) al adresei. În niciun caz textul
> adresei nu apare în `entity_id` — adresa rămâne ca nume al dispozitivului și
> ca atribut. Numele și stările entităților sunt în engleză; dialogurile de
> configurare sunt în română.

---

## Exemple de automatizări

> ID-urile entităților depind de codul tău de client și de id-ul de adresă Digi,
> așa că verifică valorile reale în **Developer Tools → States** (caută `digi`).
> Exemplele de mai jos folosesc `sensor.digi_123456_11112222_...` ca substituent
> — înlocuiește-l cu ID-urile tale.

### Notificare la apariția unei restanțe

```yaml
automation:
  - alias: "Digi — restanță"
    trigger:
      - platform: state
        entity_id: sensor.digi_123456_11112222_overdue
        to: "yes"
    action:
      - service: notify.mobile_app_telefonul_meu
        data:
          title: "Digi — factură restantă"
          message: >
            Ai de plată {{ states('sensor.digi_123456_11112222_amount_due') }} RON.
```

### Notificare la emiterea unei facturi noi

```yaml
automation:
  - alias: "Digi — factură nouă"
    trigger:
      - platform: state
        entity_id: sensor.digi_123456_11112222_amount_due
        attribute: invoice_number
    action:
      - service: notify.mobile_app_telefonul_meu
        data:
          title: "Digi — factură nouă"
          message: >
            Factura {{ state_attr('sensor.digi_123456_11112222_amount_due', 'invoice_number') }}
            în valoare de {{ states('sensor.digi_123456_11112222_last_invoice') }} RON.
```

### Card pentru Dashboard

```yaml
type: entities
title: Digi
entities:
  - entity: sensor.digi_123456_11112222_amount_due
    name: Sumă de plată
  - entity: sensor.digi_123456_11112222_due_date
    name: Scadență
  - entity: sensor.digi_123456_11112222_overdue
    name: Restanțe
  - entity: sensor.digi_123456_11112222_number_of_services
    name: Servicii facturate
```

---

## Cerințe

- **Home Assistant** 2024.12 sau mai nou (pattern `entry.runtime_data`).
- **Cont online Digi** activ (e-mail + parolă) — [digi.ro](https://www.digi.ro).
- **HACS** (opțional, pentru instalare ușoară).
- Fără dependențe externe (nu instalează pachete pip).

---

## Limitări cunoscute

1. **Bazată pe site-ul Digi** — nu există un API public, așa că integrarea
   parsează paginile din contul tău. Se poate modifica dacă Digi schimbă site-ul.
2. **Autentificare în doi pași** — necesară doar la configurare și la
   reautentificare; interogările periodice folosesc cookie-urile salvate.
3. **Istoric configurabil** — implicit sunt citite ultimele 6 facturi per
   adresă (între 1 și 24).
4. **Un cont per autentificare** — pentru mai multe conturi Digi, adaugă
   integrarea de mai multe ori; fiecare păstrează propria sesiune. Conturile se
   actualizează pe rând (round-robin), deci fiecare cont este reîmprospătat la
   fiecare (număr de conturi × interval).

---

## Confidențialitate

Datele de autentificare și sesiunile sunt stocate exclusiv local în Home
Assistant. Informațiile sensibile (e-mail, parolă, cookie-uri, adrese,
identificatori de cont și de factură) sunt eliminate automat din datele de
diagnosticare.

---

## Contribuții

Contribuțiile sunt binevenite — trimite un pull request sau raportează probleme
[aici](https://github.com/coffeerunhobby/ha-digi-rds-rcs/issues). Dacă îți este
utilă integrarea, oferă-i o ⭐ pe
[GitHub](https://github.com/coffeerunhobby/ha-digi-rds-rcs).

## Licență

[MIT](LICENSE)
