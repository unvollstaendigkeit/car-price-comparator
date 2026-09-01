/**
 * Slovak dictionary - the site default. Structure mirrors en.ts exactly.
 *
 * DRAFT COPY: written by Claude, not proofread by a native speaker yet.
 * Flag anything that reads off before this ships.
 */
export const sk = {
  locale: "sk",
  fieldNames: {
    year: "rok",
    km: "nájazd",
    price: "cena",
    fuel: "palivo",
  },
  header: {
    tagline: "Ocenenie ojazdeného auta na základe dvoch nezávislých trhovísk — vždy zobrazené oddelene, nikdy nezlúčené.",
  },
  tabs: {
    single: "Jedno auto",
    inventory: "Viac áut",
  },
  form: {
    title: "Údaje o vozidle",
    tabs: { paste: "Vložiť riadok", manual: "Vyplniť manuálne", vin: "Vyhľadať podľa VIN" },
    quickFill: "Rýchle vyplnenie:",
    paste: {
      title: "Vložte riadok z Excelu alebo Google Sheets",
      placeholder: "Sem vložte svoj riadok…",
      detect: "Rozpoznať auto",
      detecting: "Rozpoznávam…",
      tryExample: "Vyskúšať príklad",
      editPasted: "Upraviť vložený text",
      parseError: "Z tohto textu sa nepodarilo rozpoznať vozidlo. Skontrolujte riadok a skúste to znova.",
      parseFailed: "Spracovanie zlyhalo",
    },
    detected: {
      label: "Rozpoznané vozidlo",
      fallback: "Vozidlo",
      missingWarning: "Niektoré údaje sa nepodarilo rozpoznať. Skontrolujte polia nižšie.",
    },
    vin: {
      title: "Vyhľadať podľa VIN",
      description:
        "Vložte identifikačné číslo vozidla (VIN) na vyhľadanie jeho údajov v externom registri. Toto vyhľadávanie zatiaľ nie je pripojené — zatiaľ použite Vložiť riadok alebo Vyplniť manuálne nižšie.",
      lookup: "Vyhľadať VIN",
      comingSoonTitle: "Vyhľadávanie podľa VIN čoskoro",
      comingSoon: "Dostupné čoskoro",
    },
    fields: {
      brand: "Značka",
      model: "Model",
      variant: "Variant / motor",
      variantHint: "napr. 2.0 TDI 150HP",
      year: "Rok",
      fuel: "Palivo",
      km: "Nájazd (km)",
      price: "Požadovaná cena (€)",
      power: "Výkon (kW)",
      transmission: "Prevodovka",
    },
    fuelLabels: {
      Petrol: "Benzín",
      Diesel: "Diesel",
      Hybrid: "Hybrid",
      PHEV: "PHEV",
      Electric: "Elektromobil",
      LPG: "LPG",
      CNG: "CNG",
    },
    transmissionLabels: {
      Manual: "Manuálna",
      Automatic: "Automatická",
    },
    bodyTypeLabels: {
      SUV: "SUV",
      Estate: "Kombi",
      Hatchback: "Hatchback",
      MPV: "MPV",
      Sedan: "Sedan",
      Coupe: "Kupé",
      Cabriolet: "Kabriolet",
      Van: "Dodávka",
      Pickup: "Pickup",
      Liftback: "Liftback",
      Roadster: "Roadster",
    },
    none: "—",
    footerHint: "Značka a model sú povinné. Čím viac polí vyplníte, tým presnejšia bude zhoda porovnateľných vozidiel.",
    clear: "Vymazať",
    submit: "Porovnať ceny",
    submitting: "Porovnávam…",
    verifyTooltip: "Ubezpečte sa, že táto hodnota je správna",
  },
  confidence: {
    high: "Vysoká istota",
    medium: "Stredná istota",
    low: "Nízka istota",
    insufficient: "Nedostatok údajov",
  },
  diff: {
    belowMarket: "pod trhom",
    aboveMarket: "nad trhom",
    atMarket: "na úrovni trhu",
    fromAsking: "od požadovanej ceny",
  },
  tier: {
    strict: "Presná zhoda",
    moderate: "Stredná zhoda",
    broad: "Široká zhoda",
  },
  count: {
    comparables: (n: number) => {
      const abs = Math.abs(n)
      const word = abs === 1 ? "porovnateľné auto" : abs >= 2 && abs <= 4 ? "porovnateľné autá" : "porovnateľných áut"
      return `${n} ${word}`
    },
    cars: (n: number) => {
      const abs = Math.abs(n)
      const word = abs === 1 ? "auto" : abs >= 2 && abs <= 4 ? "autá" : "áut"
      return `${n} ${word}`
    },
  },
  result: {
    label: "Výsledok ocenenia",
    export: "Exportovať výsledok",
    clear: "Vymazať",
    askingPrice: "Požadovaná cena",
    coverage: {
      both: "Oba trhoviská mali dostatok porovnateľných áut na odhad trhu.",
      onlyAutobazar: "Iba Autobazar.eu malo dostatok porovnateľných áut na odhad trhu.",
      onlyBazos: "Iba Bazoš.sk malo dostatok porovnateľných áut na odhad trhu.",
      neither: "Žiadne z trhovísk nemalo dostatok porovnateľných áut na spoľahlivý odhad.",
    },
    agreement: {
      agree: { label: "zhoda zdrojov", note: "Obe trhoviská mali navzájom veľmi blízke mediánové ceny." },
      meaningful: {
        label: "čiastočná zhoda",
        note: "Trhoviská sa dostatočne líšia na to, aby ste k odhadu pristupovali opatrne.",
      },
      large: {
        label: "zdroje sa rozchádzajú",
        note: "Veľký rozdiel medzi trhoviskami — pred dôverou v ktorékoľvek z nich skontrolujte porovnateľné inzeráty.",
      },
    },
    whyConfidence: "Prečo táto istota?",
    spread: "rozdiel",
    strictMatchesFound: "Boli nájdené presné zhody",
    onlyTierMatchesFound: (tier: string) => `Boli nájdené iba zhody typu „${tier}“`,
  },
  warnings: {
    singleListing: "Odhad vychádza z jediného inzerátu — berte ho len ako orientačný, nie presný údaj.",
    implausibleRatio: "Nájdené inzeráty nevyzerali ako skutočné zhody pre toto auto, preto sme tento odhad skryli.",
    unreliable: "Tento odhad nemusí byť spoľahlivý.",
    retrievalTimeout: (name: string) => `${name} neodpovedalo včas, preto sa pre toto auto nedalo overiť.`,
    retrievalBlocked: (name: string) => `${name} sa nepodarilo načítať, preto sa pre toto auto nedalo overiť.`,
    sourceDisagreement: (a: string, b: string, pct: number) =>
      `${a} a ${b} uvádzajú pre toto auto dosť odlišné ceny (rozdiel asi ${pct} %) — oplatí sa skontrolovať oba zdroje, než niektorému uveríte.`,
  },
  card: {
    resultAriaLabel: (name: string) => `Výsledok — ${name}`,
    fetchFailedTitle: "Nepodarilo sa načítať inzeráty",
    fetchFailedTimeout: (name: string) =>
      `${name} neodpovedalo včas — ide o dočasný problém s načítaním, nie o nedostatok porovnateľných áut.`,
    fetchFailedBlocked: (name: string) =>
      `${name} sa nepodarilo načítať — ide o dočasný problém s načítaním, nie o nedostatok porovnateľných áut.`,
    showErrorDetail: "Zobraziť detail chyby",
    noneFoundTitle: "Nenašli sa žiadne porovnateľné autá",
    noneFoundBody: (name: string) =>
      `Na ${name} sa nenašli inzeráty zodpovedajúce tejto špecifikácii. Ide o nedostatok zhodných áut, nie o chybu načítania.`,
    medianAskingPrice: "Mediánová požadovaná cena",
    aboveAsking: "nad vašou požadovanou cenou",
    belowAsking: "pod vašou požadovanou cenou",
    vsAsking: "voči vašej požadovanej cene",
    yourAsking: "vaša požadovaná cena",
    smallSample: "Malá vzorka",
    comparisonDetails: "Podrobnosti porovnania",
    matchTier: "Úroveň zhody",
    priceRange: "Cenové rozpätie (P25–P75)",
    comparableMileageMedian: "Nájazd porovnateľných áut (medián)",
    comparableMileageRange: "Nájazd porovnateľných áut (P25–P75)",
    thisCarsMileage: "Nájazd tohto auta",
    comparableCars: "Porovnateľné autá",
    outliersTrimmed: "Vylúčené odľahlé hodnoty",
    missingYearKm: "Chýbajúci rok/nájazd",
    showComparableListings: (n: number) => `Zobraziť porovnateľné inzeráty (${n})`,
  },
  mileage: {
    goodMatch: "Nájazd zodpovedá porovnateľným inzerátom",
    headline: {
      very_large: "Nesúlad v nájazde",
      large: "Nájazd sa výrazne líši",
      moderate: "Nájazd sa mierne líši",
      unknown: "Nájazd neoverený",
    },
    dirLower: "nižší",
    dirHigher: "vyšší",
    dirDifferent: "odlišný",
    lead: (dirWord: string, significant: boolean, medianStr: string | null, submittedStr: string | null) =>
      `Väčšina porovnateľných áut má ${significant ? "výrazne " : ""}${dirWord} nájazd ako toto vozidlo${
        medianStr && submittedStr ? ` (medián ${medianStr} oproti ${submittedStr}).` : "."
      }`,
    comparableListings: "Porovnateľné inzeráty:",
    thisCar: "Toto auto:",
    unknownFallback: "Nájazd porovnateľných áut nebol dostupný, preto sa podobnosť nájazdu nedala overiť.",
    badgeGoodTitle: "Nájazd zodpovedá porovnateľným autám",
    badgeGood: "✓ nájazd",
    badgeVeryLarge: "⚠ nesúlad nájazdu",
    badgeLarge: "⚠ nájazd sa líši",
    badgeModerate: "nájazd sa líši",
    badgeUnknown: "nájazd n/a",
  },
  table: {
    noComparables: "Pre tento zdroj sa nenašli žiadne porovnateľné inzeráty.",
    price: "Cena",
    year: "Rok",
    mileage: "Nájazd",
    listing: "Inzerát",
  },
  progress: {
    preparing: "Príprava vozidla",
    searchingAutobazar: "Prehľadávam Autobazar.eu",
    searchingBazos: "Prehľadávam Bazoš.sk",
    comparing: "Porovnávam inzeráty",
    listings: (n: number) => {
      const abs = Math.abs(n)
      const word = abs === 1 ? "inzerát" : abs >= 2 && abs <= 4 ? "inzeráty" : "inzerátov"
      return `${n} ${word}`
    },
    footer:
      "Živé načítanie z dvoch trhovísk — zvyčajne to trvá 20 – 40 sekúnd. Výsledky sa zobrazia hneď po dokončení porovnania.",
    inProgress: "prebieha",
  },
  resultPage: {
    missingTitle: "V tomto odkaze chýbajú údaje o vozidle.",
    missingBody: "Táto stránka očakáva údaje o vozidle v URL adrese. Vráťte sa a vyplňte formulár znova.",
    backLink: "← Späť na Carval",
  },
  inventoryResultPage: {
    pageTitle: "Výsledky pre viac áut — Carval",
    notFoundTitle: "Tento výsledok už nie je v tomto prehliadači dostupný.",
    notFoundBody:
      "Výsledky pre viac áut zostávajú dostupné len v karte (a prehliadači), kde analýza prebehla — nejde o zdieľateľný odkaz. Vráťte sa a spustite analýzu znova, alebo použite Exportovať výsledky / Exportovať správu, ak chcete kópiu, ktorú môžete poslať komukoľvek.",
    backLink: "← Späť na Carval",
  },
  errors: {
    comparisonFailed: "Porovnanie zlyhalo",
    requestFailed: "Požiadavka zlyhala",
  },
  inventory: {
    couldNotReadFile: "Súbor sa nepodarilo prečítať.",
    upload: {
      title: "Nahrajte svoj sklad vozidiel",
      description:
        "Vložte tabuľku od predajcu a stĺpce rozpoznáme automaticky — funguje akékoľvek rozumné usporiadanie. Pevná šablóna nie je potrebná.",
      reading: "Načítavam tabuľku…",
      dropOrBrowse: "Vložte súbor alebo kliknite na výber",
      fileTypes: "Excel (.xlsx) alebo CSV (.csv)",
      noFile: "Nemáte poruke súbor? Vyskúšajte ukážku:",
      demoSample: "Sklad s 98 autami",
      demoAlt: "Alternatívny formát",
    },
    review: {
      carsDetected: (n: number) => {
        const abs = Math.abs(n)
        const word = abs === 1 ? "auto" : abs >= 2 && abs <= 4 ? "autá" : "áut"
        return `Rozpoznaných ${n} ${word}`
      },
      uploadDifferent: "Nahrať iný súbor",
      statReady: "Pripravené",
      statNeedsReview: "Vyžaduje kontrolu",
      statSold: "Predané / nedostupné",
      columnMapping: "Ako boli priradené vaše stĺpce",
      ignoredColumns: (list: string) => `Ignorované stĺpce: ${list}`,
      noSearchesYet: "Zatiaľ neprebehlo žiadne vyhľadávanie na trhoviskách. Pokračujte, keď rozpoznané autá vyzerajú správne.",
      continue: "Pokračovať na ocenenie →",
    },
    ready: {
      readyToAnalyze: (n: number) => {
        const abs = Math.abs(n)
        const word = abs === 1 ? "auto" : abs >= 2 && abs <= 4 ? "autá" : "áut"
        return `Pripravené na analýzu — ${n} ${word}`
      },
      description:
        "Každé auto sa oceňuje voči Autobazar.eu a Bazoš.sk — rovnaké porovnanie z dvoch zdrojov ako v režime Jedno auto. Autá sú zoskupené podľa značky a modelu, takže spoločné vyhľadávanie znižuje počet požiadaviek a oba zdroje zostávajú oddelené (nikdy sa nezlučujú). Trhové dáta pre každý model sa uchovávajú v cache 24 hodín, takže opakované behy ich použijú znova bez nových požiadaviek.",
      refreshLabel: "Obnoviť trhové dáta",
      refreshHint: "— ignorovať cache a načítať každý model naživo",
      backToReview: "Späť na kontrolu",
      startOver: "Začať odznova",
      run: "Spustiť analýzu trhu →",
    },
    analysis: {
      justNow: "práve teraz",
      minutesOld: (n: number) => `pred ${n} min`,
      hoursOld: (n: number) => `pred ${n} h`,
      daysOld: (n: number) => `pred ${n} d`,
      statusRunning: "Analyzujem sklad",
      statusDone: "Analýza dokončená",
      statusStopped: "Analýza zastavená",
      carsValued: (analyzed: number, total: number) => `${analyzed} / ${total} ocenených áut`,
      exportResults: "Exportovať výsledky",
      exportResultsTitle: "Stiahnuť Excel zošit so všetkými analyzovanými autami (Autobazar.eu a Bazoš.sk oddelene)",
      exportReport: "Exportovať správu",
      exportReportTitle: "Stiahnuť samostatnú HTML správu (otvorte v ľubovoľnom prehliadači, alebo Tlačiť → Uložiť ako PDF)",
      openResults: "Otvoriť výsledky",
      openResultsTitle: "Otvoriť prehľadné zobrazenie výsledkov na novej karte",
      refreshMarketData: "Obnoviť trhové dáta",
      refreshMarketDataTitle: "Ignorovať uložené trhové dáta a znova načítať každý model naživo",
      back: "Späť",
      startOver: "Začať odznova",
      reusingCacheFor: "Používam uložené dáta pre",
      searching: "Vyhľadávam",
      modelProgress: (i: number, n: number) => `model ${i}/${n}`,
      budgetTitle: (s: number) => `Časový rozpočet na model: ${s}s`,
      cacheLiveLookups: (cached: number, live: number, lookups: number) =>
        `${cached} z cache · ${live} naživo · ${lookups} živých požiadaviek`,
      timeouts: (n: number) => {
        const abs = Math.abs(n)
        const word = abs === 1 ? "časový limit" : abs >= 2 && abs <= 4 ? "časové limity" : "časových limitov"
        return `${n} ${word} — pokračujem`
      },
      modelsOverBudget: (n: number) => {
        const abs = Math.abs(n)
        const word = abs === 1 ? "model prekročil" : abs >= 2 && abs <= 4 ? "modely prekročili" : "modelov prekročilo"
        return `${n} ${word} rozpočet`
      },
      statHigh: "Vysoká",
      statMedium: "Stredná",
      statLow: "Nízka",
      statInsufficient: "Nedostatočná",
      statLiveRequests: "Živé požiadavky",
      statModelsFromCache: "Modely z cache",
      statTimeoutsContinued: "Časové limity (pokračovalo sa)",
      statModelsOverBudget: "Modely nad rozpočtom",
      statSourceErrors: "Chyby zdrojov",
      timingTotal: "Celkovo",
      timingRetrieval: "Načítanie",
      timingValuation: "Ocenenie",
      perReq: (s: string) => ` (${s}/požiadavku)`,
      cacheLogTitle: "Zdroj trhových dát podľa modelu",
      cached: "z cache",
      fetchedLive: "načítané naživo",
      sourceTimeout: (src: string, brand: string, model: string, kept: string) =>
        `${src} vypršal časový limit pri ${brand} ${model} — pokračujem${kept}.`,
      keptPartial: (n: number) => {
        const abs = Math.abs(n)
        const word = abs === 1 ? "čiastočný inzerát" : abs >= 2 && abs <= 4 ? "čiastočné inzeráty" : "čiastočných inzerátov"
        return ` (ponechaných ${n} ${word})`
      },
      groupTimeout: (brand: string, model: string, elapsed: number, budget: number, keptTxt: string) =>
        `${brand} ${model}: PREKROČENÝ ČASOVÝ ROZPOČET (${elapsed}s z ${budget}s) — pokračujem ďalej.${keptTxt}`,
      keptPartialSentence: (n: number) => {
        const abs = Math.abs(n)
        const word = abs === 1 ? "čiastočný inzerát" : abs >= 2 && abs <= 4 ? "čiastočné inzeráty" : "čiastočných inzerátov"
        return ` Ponechaných ${n} ${word}.`
      },
      analysisFailed: "Analýza zlyhala.",
      rankedHint: "Zoradené od najviac pod trhom po najmenej. Kliknutím na riadok zobrazíte podrobnosti zdrojov.",
    },
    reviewTable: {
      statusReady: "Pripravené",
      statusSold: "Predané",
      statusReview: "Vyžaduje kontrolu",
      all: "Všetko",
      needAttention: (n: number) => {
        const abs = Math.abs(n)
        const word = abs === 1 ? "riadok vyžaduje" : abs >= 2 && abs <= 4 ? "riadky vyžadujú" : "riadkov vyžaduje"
        return `${n} ${word} pozornosť pred ocenením. Zostávajú v zozname, nie sú odstránené — opravte ich v tabuľke a nahrajte znova, alebo pokračujte bez nich.`
      },
      colBrand: "Značka",
      colModel: "Model",
      colVariant: "Variant",
      colYear: "Rok",
      colFuel: "Palivo",
      colKm: "Km",
      colPrice: "Cena",
      colStatus: "Stav",
      priceMissing: "chýba",
      noRows: "V tejto kategórii nie sú žiadne riadky.",
    },
    resultsTable: {
      noRanking: "Žiadne auto neprodukovalo použiteľné trhové poradie. Dôvod nájdete v poznámkach pri jednotlivých autách (nedostatok porovnateľných áut alebo blokované zdroje).",
      colVehicle: "Vozidlo",
      colAsking: "Požadovaná",
      colConfidence: "Istota",
      blocked: "blokované",
      noData: "žiadne dáta",
      noComparableListings: "Žiadne porovnateľné inzeráty.",
      median: "Medián",
      whyConfidence: "Prečo táto istota: ",
      medianSpread: "Rozdiel mediánov medzi zdrojmi: ",
      shownNeverAveraged: (pct: number) => `${pct} % (zobrazené, nikdy nespriemerované)`,
      missingFields: "Chýbajúce polia v sklade: ",
    },
  },
  exportInv: {
    mileageLabel: {
      good: "Zodpovedá porovnateľným",
      moderate: "Mierne odlišný",
      large: "Výrazne odlišný",
      very_large: "Nesúlad",
      unknown: "Neoverený",
    },
    insufficientData: "Nedostatok porovnateľných dát",
    sourceBlocked: (label: string) => `${label}: blokované`,
    sourceError: (label: string) => `${label}: chyba`,
    sourceNoComparables: (label: string) => `${label}: žiadne porovnateľné autá`,
    sourceMileage: (label: string, mileageLower: string) => `${label}: nájazd ${mileageLower}`,
    headers: {
      vehicle: "Vozidlo",
      brand: "Značka",
      model: "Model",
      variant: "Variant",
      year: "Rok",
      fuel: "Palivo",
      mileageKm: "Nájazd (km)",
      askingPriceEur: "Požadovaná cena (€)",
      abMedian: "Medián Autobazar.eu (€)",
      abDiffEur: "Rozdiel Autobazar.eu (€)",
      abDiffPct: "Rozdiel Autobazar.eu (%)",
      abComparables: "Porovnateľné autá Autobazar.eu",
      bzMedian: "Medián Bazoš.sk (€)",
      bzDiffEur: "Rozdiel Bazoš.sk (€)",
      bzDiffPct: "Rozdiel Bazoš.sk (%)",
      bzComparables: "Porovnateľné autá Bazoš.sk",
      confidence: "Istota",
      mileageSimilarity: "Podobnosť nájazdu",
      warnings: "Upozornenia / nedostatok dát",
    },
    compsHeaders: {
      vehicle: "Vozidlo",
      source: "Zdroj",
      listingPrice: "Cena inzerátu (€)",
      year: "Rok",
      mileageKm: "Nájazd (km)",
      listingTitle: "Názov inzerátu",
      listingUrl: "URL inzerátu",
    },
    blocked: "blokované",
    noData: "žiadne dáta",
    mileagePrefix: "Nájazd:",
    compsPrefix: "porovnateľné",
    medianLabel: "Medián",
    whyConfidence: "Prečo táto istota:",
    medianSpread: "Rozdiel mediánov medzi zdrojmi:",
    shownNeverAveraged: (pct: number) => `${pct} % (zobrazené, nikdy nespriemerované)`,
    missingFields: "Chýbajúce polia v sklade:",
    docTitle: (n: number) => `Carval — výsledky skladu (${n})`,
    docMeta: (date: string) => `Výsledky skladu · vygenerované ${date}`,
    heading: (n: number) => {
      const abs = Math.abs(n)
      const noun = abs === 1 ? "vozidlo" : abs >= 2 && abs <= 4 ? "vozidlá" : "vozidiel"
      const participle = abs >= 5 || abs === 0 ? "zoradených" : "zoradené"
      return `${n} ${noun} ${participle} — od najviac pod trhom po najmenej`
    },
    hint: "Kliknutím na riadok zobrazíte podrobnosti zdrojov a porovnateľné inzeráty. Autobazar.eu a Bazoš.sk sú zobrazené oddelene — nikdy sa nezlučujú.",
    tableHeaders: { rank: "#", vehicle: "Vozidlo", asking: "Požadovaná", confidence: "Istota", mileage: "Nájazd" },
    chipAnalyzed: "Analyzované",
    chipHigh: "Vysoká",
    chipMedium: "Stredná",
    chipLow: "Nízka",
    chipInsufficient: "Nedostatočná",
    sheetInventory: "Sklad",
    sheetComparables: "Porovnateľné inzeráty",
  },
  exportSingle: {
    printButton: "Tlačiť / Uložiť ako PDF",
    docMeta: (date: string) => `Správa o ocenení · vygenerované ${date}`,
    vehicleDetails: "Údaje o vozidle",
    fields: {
      brand: "Značka",
      model: "Model",
      variant: "Variant",
      year: "Rok",
      fuel: "Palivo",
      mileage: "Nájazd",
      askingPrice: "Požadovaná cena",
      power: "Výkon",
      transmission: "Prevodovka",
      bodyType: "Karoséria",
    },
    overallAssessment: "Celkové hodnotenie",
    agreement: {
      agree: "Zdroje sa zhodujú — obe trhoviská mali navzájom veľmi blízke mediánové ceny.",
      meaningful: "Čiastočná zhoda — trhoviská sa dostatočne líšia na to, aby ste k tomu pristupovali opatrne.",
      large: "Zdroje sa rozchádzajú — pred dôverou v ktorýkoľvek z nich skontrolujte porovnateľné inzeráty na oboch.",
    },
    spreadTail: (pct: number, gap: number | null) =>
      gap !== null ? `(rozdiel mediánov ${pct} %, rozdiel v ocenení ${gap} %).` : `(rozdiel mediánov ${pct} %).`,
    sourcesHeading: "Autobazar.eu a Bazoš.sk — zobrazené oddelene, nikdy nezlúčené",
    footer:
      "Každé trhovisko sa hodnotí nezávisle — Carval ich nikdy nezlučuje do jedného čísla. „Pod trhom“ znamená cenu nižšiu, než je mediánová požadovaná cena na danom trhu. Hlavná istota vychádza z trhoviska so silnejšou porovnateľnou vzorkou.",
    notEnoughListings: (n: number) => `Nedostatok porovnateľných inzerátov pre spoľahlivý odhad (nájdených: ${n}).`,
    marketMedian: "Mediánová cena na trhu",
    priceRange: "P25 – P75",
    diffVsAsking: "Rozdiel voči požadovanej cene",
    assessment: "Hodnotenie",
    mileageSimilarity: "Podobnosť nájazdu:",
    mileageDetail: (compRange: string, median: string, thisCar: string) =>
      ` · porovnateľné ${compRange} (medián ${median}), toto auto ${thisCar}`,
    viewListing: "Zobraziť inzerát",
    noComparablesCaptured: "Nezachytili sa žiadne porovnateľné inzeráty.",
    tableHeaders: { price: "Cena", year: "Rok", mileage: "Nájazd", title: "Názov", link: "Odkaz" },
  },
}
