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
- Dispozitive separate pentru fiecare adresă și serviciu monitorizat.
- Senzori pentru: suma de plată, valoarea ultimei facturi, data scadenței și
  existența restanțelor.
- Senzori agregați la nivel de cont: total de plată, următoarea scadență,
  numărul serviciilor active și existența restanțelor.
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
5. Selectează adresa dorită dacă ai mai multe locații asociate contului.

Intervalul de actualizare și numărul de facturi citite pot fi modificate
ulterior din **Configure** (⚙️), fără a reinstala integrarea.

> Pentru mai multe conturi Digi, adaugă integrarea de mai multe ori — fiecare
> cont primește propria sesiune și propriile dispozitive.

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

Senzorul *Amount due* include atribute detaliate: serviciile facturate,
numărul facturii, datele de emitere și scadență, statusul, valoarea facturii,
linkul către PDF, defalcarea pe servicii și istoricul complet al facturilor.

> ℹ️ ID-ul fiecărei entități folosește un hash (md5) al adresei, nu textul
> adresei — astfel adresa nu apare în `entity_id`. Adresa rămâne ca nume al
> dispozitivului și ca atribut. Numele și stările entităților sunt în engleză;
> dialogurile de configurare sunt în română.

---

## Exemple de automatizări

> ID-urile entităților depind de adresa și serviciile tale, așa că verifică
> valorile reale în **Developer Tools → States** (caută `digi`). Exemplele de
> mai jos sunt orientative — înlocuiește `sensor.digi_cont_...` cu ID-urile tale.

### Notificare la apariția unei restanțe

```yaml
automation:
  - alias: "Digi — restanță"
    trigger:
      - platform: state
        entity_id: sensor.digi_cont_has_overdue
        to: "yes"
    action:
      - service: notify.mobile_app_telefonul_meu
        data:
          title: "Digi — factură restantă"
          message: >
            Ai de plată {{ states('sensor.digi_cont_total_amount_due') }} RON.
```

### Notificare la emiterea unei facturi noi

```yaml
automation:
  - alias: "Digi — factură nouă"
    trigger:
      - platform: state
        entity_id: sensor.digi_cont_total_amount_due
        attribute: last_invoice_id
    action:
      - service: notify.mobile_app_telefonul_meu
        data:
          title: "Digi — factură nouă"
          message: >
            Factura {{ state_attr('sensor.digi_cont_total_amount_due', 'last_invoice_id') }}
            în valoare de {{ states('sensor.digi_cont_total_last_invoice') }} RON.
```

### Card pentru Dashboard

```yaml
type: entities
title: Digi
entities:
  - entity: sensor.digi_cont_total_amount_due
    name: Total de plată
  - entity: sensor.digi_cont_next_due_date
    name: Următoarea scadență
  - entity: sensor.digi_cont_has_overdue
    name: Restanțe
  - entity: sensor.digi_cont_number_of_services
    name: Servicii active
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
