# crate — audyt UX obsługi duplikatów

Audyt na żywej instancji `127.0.0.1:8899`, 26.07.2026. Wszystkie liczby poniżej pochodzą
z Twoich endpointów, nie z opisu.

---

## Werdykt w trzech zdaniach

Backend ma komplet danych do rozstrzygnięcia każdego duplikatu. UI nie pokazuje z nich
prawie niczego. Nie masz problemu z dedupem — masz problem z jednym brakującym ekranem.

---

## 1. Gdzie proces gubi człowieka

### 1.1. Wskaźnik na oryginał istnieje i jest nieużywalny

Każdy z 186 plików ze statusem `duplicate` ma w danych pole `dup_of`. Sprawdziłem
wszystkie: **186 z 186 wskaźników rozwiązuje się poprawnie** na plik obecny w tym samym
indeksie. Dane są kompletne, w 100%.

W UI ten wskaźnik pojawia się dokładnie raz — w atrybucie `title`:

```html
<span class="orig-dup-badge" title="Duplicate of 77bd4b9a-29ff-5634-b267-52a0d019dd79">
```

To UUID. Pod kursorem. Nie da się go kliknąć, zaznaczyć, skopiować ani wyszukać —
wkleiłem go w pole `orig-search` i dostałem „No files match the current filters".
Ślepy zaułek zaimplementowany celowo, w jednym atrybucie.

**To jest moment, w którym proces Cię gubi.** Filtrujesz `Status: Duplicate`, dostajesz
186 wierszy, każdy krzyczy DUPLICATE (dwa razy — raz w kolumnie STATUS, raz jako plakietka
obok; ta redundancja to osobny drobiazg), i nie ma żadnej drogi do drugiego pliku.
Pytanie „który jest lepszy?" nie ma w tym UI reprezentacji.

### 1.2. Widok pokazuje przegranych, nigdy zwycięzców

Filtr `duplicate` z definicji wyklucza z listy plik, który zostaje. Nie ma trybu, w którym
widzisz obie strony naraz. Nawet gdybyś ręcznie odnalazł partnera, musisz go trzymać w
głowie — bo nie zmieścisz obu w jednym widoku.

Do tego zwycięzcy mają różne statusy: **34 z 186** wskazuje na plik `in_library`,
**152 na `not_sent`**. Czyli „duplicate" nie znaczy „już to masz w Library". Znaczy
„gdzieś na NAS-ie leży drugi taki plik, o którym nic ci nie powiem". Jedno słowo,
dwa różne znaczenia, żadnego wyjaśnienia w interfejsie.

### 1.3. Rekomendacja jest arbitralna i bywa błędna

Sprawdziłem jakość obu stron każdej pary:

| | |
|---|---|
| pary o identycznym rozmiarze | 115 |
| pary o **różnym** rozmiarze (różnica 48–128 B, czyli padding tagów ID3) | 71 |
| pary o różnej etykiecie jakości | **0** |
| pary o **różnej nazwie pliku** | **62** |
| przypadki, w których **odrzucany plik ma bogatsze tagi** niż zachowywany | **6** |
| pary, w których oba pliki leżą w innym katalogu | 177 |

Dwie rzeczy z tego wynikają.

Po pierwsze: **71 z 186 par nie jest identycznych co do bitu.** Dedup działa na hashu
audio, nie na hashu pliku — to dobra decyzja inżynierska, tylko nigdzie niezakomunikowana.
Twoje własne założenie („180 grup identycznych co do bitu") jest niezgodne z tym, co robi
Twoja aplikacja. Skoro autor nie wie, to UI nie mówi.

Po drugie: w 6 przypadkach plik oznaczony do odrzucenia ma **lepsze metadane** niż ten,
który zostaje. Przykład z Twoich danych:

```
Clean Bandit feat. Sean Paul & Anne-Marie - Rockabye.mp3
  oznaczony DUPLICATE   (HITY)      artysta: "Clean Bandit feat. Sean Paul & Anne-Marie"
                                    tytuł:   "Rockabye"
  zostaje               (2017 May)  artysta: "NRJ"
                                    tytuł:   ""
```

Zwycięzcą jest plik otagowany przez rozgłośnię radiową jako „NRJ", bez tytułu.
Nikt Ci tego nie pokazuje. Gdyby istniał skrypt kasujący, straciłbyś lepszy plik.

A do tego 62 pary mają różne nazwy — dla DJ-a nazwa pliku to uchwyt, po którym szukasz
w Rekordboksie. Wybór między „Loo & Placido - Future Sound (Nero & Knife Party vs.
Gorillaz vs. Bassnectar).aif" a „Loo & Placido - Future Sound.aif" to realna decyzja,
nie szum.

### 1.4. Nie ma czynności „kasuję ten plik". W ogóle.

Przejrzałem menu kontekstowe wiersza (11 pozycji: re-enrich, swap artist/title, suggest
genre, identify track, search Google/Beatport/SoundCloud, show in Finder, copy filename…),
menu `⋯` w nagłówku (jedna pozycja: „Unapply last run") i pasek akcji w Original.
Nigdzie nie ma akcji dotyczącej duplikatu.

Jedyna kontrolka to checkbox w stopce: **„Uwzględnij duplikaty (0)"** — pozwala wysłać
duplikat do Unsorted mimo wszystko. To nie jest rozstrzygnięcie, to obejście.

Pytasz, jak ma wyglądać ekran, na którym decydujesz „kasuję ten plik" i się nie boisz.
Odpowiedź zaczyna się od tego, że **taki ekran nie istnieje i nie ma nawet przycisku,
który by go otwierał**. Strach nie bierze się z braku zaufania do aplikacji. Bierze się
z tego, że aplikacja nie oferuje tej decyzji, więc musisz ją podjąć poza nią — w Finderze,
po omacku, bez danych, które przed chwilą oglądałeś.

### 1.5. Licznik postępu liczy duplikaty jako pracę do zrobienia

`1300 z 5527 przejrzane (24%)`. Mianownik zawiera 186 duplikatów, które nigdy nie zostaną
`in_library`. Pasek nie może dojść do 100%. Drobiazg, ale w narzędziu, którego jedynym
celem jest doprowadzenie kolejki do zera, licznik, który strukturalnie nie może się
domknąć, demotywuje przez cały czas użytkowania.

`/api/original/stats` zwraca `bytes_in_library`, `bytes_not_sent`, `bytes_sent` —
i **nie zwraca `bytes_duplicate`**. Jedyna liczba, która motywuje do sprzątania
(1,64 GB), nie jest nigdzie policzona. Policzyłem ją z surowych danych w jednej linijce.

---

## 2. Czy dwa oddzielne systemy dedupu to błąd

Nie dwa. **Trzy.** I to jest odpowiedź.

| # | Gdzie | Pola w danych | UI | Ile przypadków dziś |
|---|---|---|---|---|
| 1 | Original (indeks NAS) | `dup_of`, `status=duplicate` | tooltip z UUID | **186** |
| 2 | Unsorted (wewnątrz kolejki) | `is_duplicate`, `duplicate_paths`, `near_duplicate_of` | plakietka `D:n` → rozwijana siatka porównania z rekomendacją i przyciskami keep/reject | **0** |
| 3 | Unsorted ↔ Library | `conflict_library_*` | pełny modal: dwie strony, przyciski odsłuchu, pasek jakości, wynik punktowy, werdykt `new-better` / `lib-better` / `eq` | **0** |

Przeczytałem CSS wszystkich trzech. Mechanizmy 2 i 3 są zaprojektowane porządnie —
`.dup-grid-cell.dup-diff` podświetla różniące się wartości, `.dup-rec-badge` oznacza
rekomendację, `.conflict-verdict` wypisuje uzasadnienie, `.conflict-play-btn` pozwala
posłuchać obu wersji przed decyzją. To są dokładnie te wzorce, których potrzebujesz.

**Jakość projektu jest odwrotnie proporcjonalna do częstości występowania problemu.**
Najlepszy interfejs (modal konfliktu) obsługuje 0 przypadków. Najgorszy (tooltip)
obsługuje 186. Zbudowałeś trzy razy to samo, za każdym razem inaczej, i najlepszą wersję
podpiąłeś pod ścieżkę, która nigdy się nie odpala.

Czy rozdział etapów jest uzasadniony? Częściowo tak:

- Original ↔ Original to pytanie **„czego w ogóle nie kopiować z NAS-a"**
- Unsorted ↔ Library to pytanie **„czym podmienić to, co już mam"**

To są różne decyzje z różnymi konsekwencjami i mogą mieć różne ekrany. Ale nie mogą mieć
trzech różnych języków wizualnych, trzech różnych słów na to samo (`duplicate` /
`~Dup` / `CNFL`) i trzech różnych poziomów szczegółowości. Dziś musisz nauczyć się
aplikacji trzy razy.

### 2.1. Trzeci system nie ma szans zadziałać

Near-dup w Unsorted opiera się na fingerprintcie (Chromaprint) i długości. Sprawdziłem
Twoją bieżącą kolejkę Unsorted — 8 plików. **Fingerprint ma dokładnie jeden z nich**
(ten po ENRICH). Pozostałe siedem ma puste `fingerprint`, `duration_seconds`
i `audio_quality`.

Czyli: detektor near-dupów **strukturalnie nie może zadziałać na świeżo zaimportowanym
pliku**, bo dane, których potrzebuje, pojawiają się dopiero po ręcznym ENRICH.
Kolumna `~Dup` jest pusta dokładnie tam, gdzie jest najbardziej potrzebna.

A na ekranie masz w tej chwili, w liście ośmiu pozycji, dwa wiersze:

```
2  Alex Warren  Ordinary  (TBS & The Twinz Rework)  2025  118 BPM  ★★★★★   ~Dup: —
4  Alex Warren  Ordinary                            2025  168 BPM  ★★★★★   ~Dup: —
```

Człowiek widzi to w sekundę. Aplikacja mówi „—" dwa razy.

### 2.2. W Library detekcja jest niemożliwa z braku danych

Library: 2091 utworów, 1695 z fingerprintem, 1446 z hashem. Oflagowanych jako duplikat
lub near-duplikat: **0**. Znalazłem tam 69 grup o tym samym artyście i tytule.

Powód, dla którego reguła „±5 s" nie może tam zadziałać: **`duration_seconds` ma
6 utworów z 2036**. Pole zwyczajnie nie przeżywa przejścia Unsorted → Library.
Twoja najważniejsza kategoria jest niewykrywalna nie dlatego, że reguły brakuje,
tylko dlatego, że dane wyparowują po drodze.

---

## 3. Brakująca kategoria — i dlaczego jej brak nie jest wymówką

Mówisz, że „ten sam utwór w innej jakości" nie ma w apce reprezentacji. Zgoda.
Ale ta kategoria jest **w pełni policzalna z danych, które już serwujesz**.

Indeks Original ma `duration_seconds` dla **5526 z 5527** plików i `audio_quality`
dla **5526 z 5527**. Uruchomiłem Twoją regułę (ten sam artysta+tytuł, długość ±5 s,
pominąć pary już złapane przez hash) w konsoli przeglądarki, na `/api/original/tracks`:

- **192 pary** tego samego utworu w granicy ±5 s
- **117 par** o różnej jakości
- **191 plików** objętych

Przy ostrzejszej normalizacji nazw (ta, którą wpisałem w prototyp) wychodzi **62 grupy**;
Twoje 153 dostaniesz, luzując dopasowanie nazwy o `feat.` / `remix` / `radio edit`.
Rząd wielkości się zgadza, próg jest kwestią jednej stałej.

Czas wykonania: kilkadziesiąt milisekund na 5527 wierszach, po stronie klienta,
w vanilla JS. **Nie potrzebujesz nowego skanu, nowego pola w CSV ani nowego endpointu.**
Potrzebujesz tych trzydziestu linijek i miejsca, żeby pokazać wynik.

Przykłady z Twojej biblioteki:

```
Stromae — Papaoutai        MP3 320   9,3 MB  (root)   ⟷  FLAC  28,1 MB  (Inne)
Duke Dumont — Won't Look…  MP3 242   8,2 MB  (root)   ⟷  MP3 320  11,0 MB  (news)
Beggin (original version)  MP3 320   8,7 MB  (2019 August) ⟷ MP3 256  7,0 MB (hity stare)
```

---

## 4. Jak ma wyglądać ekran, na którym się nie boisz

Załączam działający prototyp: **`dedup-review.html`**. Nie mockup — jednoplikowy
vanilla JS, zero zależności, zero build stepu, czyta wyłącznie `/api/original/tracks`.
Przetestowałem go na Twoich danych: wykrywa 180 grup identycznych i 62 grupy
„ten sam utwór, inna jakość", razem **2,05 GB** do odzyskania.

Instalacja: wrzuć do `static/`, otwórz `http://127.0.0.1:8899/static/dedup-review.html`.
(Musi być z tego samego origin — inaczej CORS zablokuje fetch.)

Osiem zasad, na których jest zbudowany. To jest właściwa odpowiedź na Twoje pytanie.

**1. Jednostką pracy jest grupa, nie wiersz.**
Tabela 186 wierszy to zła reprezentacja problemu, który ma 180 elementów po 2–3 pliki.
Ekran pokazuje jedną grupę naraz, dużą. Lista po lewej to nawigacja, nie miejsce decyzji.

**2. Obie strony zawsze widoczne, różnice podświetlone.**
Karty obok siebie, te same pola w tej samej kolejności. Wartości, które się różnią,
świecą na bursztynowo. Reszta jest wyszarzona. Oko idzie prosto do rozstrzygającej
różnicy — nie musisz porównywać wiersz po wierszu.

**3. Rekomendacja mówi DLACZEGO, nie tylko CO.**
Zielona ramka to za mało. Pod kartami leci zdanie: „wyższa jakość (FLAC) · pełniejsze
tagi". Bez uzasadnienia rekomendacja jest kolejnym nieprzejrzystym autorytetem, któremu
nie masz powodu ufać — a Ty właśnie z tego powodu się boisz.

**4. Ryzyko jest wypisane wprost.**
Gdy odrzucany plik ma coś lepszego, dostajesz bursztynowe ostrzeżenie:
„odrzucany X ma BOGATSZE tagi", „różne nazwy plików — sprawdź, której szukasz
w Rekordboksie". Te 6 przypadków, które dziś przeszłyby po cichu, zatrzyma Cię tutaj.

**5. Nic się nie kasuje. Nigdy.**
Zatwierdzenie zapisuje decyzję lokalnie. Na końcu pobierasz `dedup-plan.json`
i uruchamiasz osobny skrypt, który **przenosi** pliki do `_dupes_trash/` na NAS-ie
razem z manifestem odwrotnym. Odzyskujesz miejsce dopiero, gdy sam opróżnisz kosz —
po tygodniu grania z nowej biblioteki. Tak wygląda „nie boję się": operacja jest
odwracalna aż do momentu, w którym sam zdecydujesz, że nie musi być.

**6. Widać nagrodę.**
Licznik w prawym górnym rogu: „odzyskane 0 MB · z 2,05 GB możliwych". Grupy posortowane
malejąco po wadze. Pierwszych dwadzieścia decyzji daje ~350 MB. Zaczynasz od największej
wygranej, nie od alfabetu.

**7. Klawiatura, bo to 180 decyzji.**
`J`/`K` nawigacja, `1`–`9` wybór zostającego, `Enter` zatwierdź, `N` „to nie duplikat",
`S` pomiń, `U` cofnij. Mysz jest opcjonalna. 180 decyzji przy 5 sekundach każda to
kwadrans — ale tylko jeśli nie trzeba celować kursorem.

**8. Wyjście awaryjne jest równorzędne z zatwierdzeniem.**
„To nie duplikat" (`N`) stoi obok „Zatwierdź", nie schowane w menu. Przy near-dupach
to nie jest przypadek brzegowy — remiks, radio edit i extended mix mają tę samą długość
±5 s i tę samą nazwę. Każde narzędzie, które karze za wątpliwość, uczy klikać „dalej".

### Czego prototyp nie ma, a mieć powinien

**Odsłuchu.** Przycisk jest, ale nieaktywny. Sprawdziłem: `/api/audio?path=` nie obsługuje
indeksu Original — rekordy mają `filename` + `folder` + `area`, nie mają pełnej ścieżki,
a endpoint zwraca 404. To jest jedna z ważniejszych rzeczy do dorobienia po stronie
Flaska: **DJ decyduje uchem**. Bez odsłuchu przy 117 parach „inna jakość" nadal będziesz
zgadywać, czy MP3 242 to zły rip, czy tylko VBR.

---

## 5. Poprawki czy projekt od nowa

**Poprawki.** I to nie z uprzejmości.

Dowód: prototyp z załącznika działa na Twojej instancji, nie tknąwszy backendu.
Wszystko, czego potrzebuje, już wychodzi z `/api/original/tracks`. Gdyby to był problem
architektoniczny, nie dałoby się tego zrobić w jednym pliku w jedno popołudnie.

Problem jest w warstwie prezentacji, a konkretnie w jednej brakującej rzeczy: **grupa
duplikatów nie jest w tej aplikacji obiektem pierwszej klasy.** Jest atrybutem wiersza
(`status=duplicate`) plus wskaźnikiem schowanym w tooltipie. Wprowadź pojęcie grupy —
i trzy niespójne mechanizmy stają się trzema widokami tej samej rzeczy.

Kolejność, od największego zwrotu:

| # | Zmiana | Koszt | Efekt |
|---|---|---|---|
| 1 | Wpiąć `dedup-review.html` jako zakładkę „Duplikaty" | godzina | 186 spraw przestaje być ślepym zaułkiem; 2,05 GB staje się policzalne |
| 2 | Zamienić tooltip z UUID na klikalny link do partnera | ~20 linii | tabela Original przestaje kłamać, że nie wie |
| 3 | `bytes_duplicate` w `/api/original/stats`, wyłączyć duplikaty z mianownika paska postępu | ~10 linii backendu | pasek może dojść do 100%, sprzątanie dostaje liczbę |
| 4 | `/api/audio` dla indeksu Original | mała zmiana we Flasku | odsłuch A/B — warunek konieczny dla kategorii „inna jakość" |
| 5 | Propagować `duration_seconds` i `audio_quality` do Library | zmiana w potoku | odblokowuje wykrywanie near-dupów w docelowej bibliotece (69 podejrzanych grup czeka) |
| 6 | Ujednolicić trzy UI do jednego komponentu grupy | dzień | jedno pojęcie, jeden język, jedna nauka zamiast trzech |
| 7 | Liczyć fingerprint przy imporcie, nie przy ENRICH | zależy od potoku | `~Dup` przestaje być pustą kolumną w kolejce Unsorted |

Punkty 1–3 to jedno popołudnie i one załatwiają całą sprawę, o którą pytasz.

---

## Jedna rzecz, którą warto zapamiętać

Ta aplikacja wie o Twojej bibliotece wszystko, czego potrzeba: który plik jest kopią
którego, o ile lepszy, w którym katalogu, z jakimi tagami, ile miejsca zwolni.
Trzyma to w `dup_of`, w `size_bytes`, w `tag_artist`, w `duration_seconds` —
i pokazuje z tego jedno słowo: `duplicate`.

Nie zbudowałeś złego dedupu. Zbudowałeś dobry dedup i nie dołożyłeś ekranu,
na którym on cokolwiek znaczy.
