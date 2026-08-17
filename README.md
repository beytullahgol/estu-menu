# ESTÜ Yemekhane Menüsü

Bu repository, Eskişehir Teknik Üniversitesi Sağlık, Kültür ve Spor Daire Başkanlığı sayfalarındaki **Ana Yemekhane** aylık PDF’i ile **Akademik Kulüp** haftalık PDF’ini okuyup iOS Kestirmeleri’nin doğrudan tüketebileceği bir JSON dosyası üretir.

## JSON adresi

Öncelikli Kestirmeler adresi:

```text
https://raw.githubusercontent.com/beytullahgol/estu-menu/main/data/menu.json
```

Telefon veya ağ raw GitHub adresinde `429: Too Many Requests` gösterirse aşağıdaki GCore jsDelivr adresini kullanın. `@latest`, her başarılı production yayınında oluşturulan semver tag’ine yönelir; bu nedenle uzun süre cache’lenebilen `@main` branch aliasından daha güvenilirdir:

```text
https://gcore.jsdelivr.net/gh/beytullahgol/estu-menu@latest/data/menu.json
```

GitHub Pages adresi de alternatif olarak kullanılabilir:

```text
https://beytullahgol.github.io/estu-menu/data/menu.json
```

Workflow her veri değişikliğinde `v0.0.<workflow-run>` biçiminde yeni bir semver tag’i oluşturur ve hem `@latest` hem de `@main` alias cache’lerini temizlemeyi dener. Kestirmeler’de fallback olarak `@latest` adresini kullanın; branch aliası eski snapshot gösterirse `@latest` güncel immutable release’e yönelir. `cdn.jsdelivr.net` ve `fastly.jsdelivr.net` bölgeler arasında zaman aşımına uğrayabildiği için GCore adresi önerilir.

Repository’nin ana sayfası da bu endpoint’e bağlantı verir:

```text
https://beytullahgol.github.io/estu-menu/
```

## Güncelleme mantığı

Collector artık kaynak bazlı çalışır. **Salı–pazar** günlerinde günlük workflow yalnızca repository’deki cache’lenmiş PDF’lerden JSON üretir; ESTÜ’ye HTML veya PDF isteği göndermez. **Pazartesi** günlerinde yalnızca Akademik Kulüp kaynağı, **ayın 1’inde** ise yalnızca Ana Yemekhane kaynağı kontrol edilir. Ayın 1’i hafta sonuna denk gelirse ana yemekhane kontrolü ilk uygun pazartesiye bırakılır.

GitHub Actions UTC kullandığı için zamanlama Türkiye saatine yaklaşık olarak şöyledir:

| Çalışma | Türkiye saati | Dış kaynak davranışı |
|---|---|---|
| Günlük cache-only | 08:05, Salı–Pazar | ESTÜ isteği yok |
| Akademik Kulüp yenileme | Pazartesi 08:03–16:03, 30 dakikada bir | Yalnızca yeni haftalık PDF bulunana kadar kontrol |
| Ana Yemekhane yenileme | Ayın 1’i 08:03–16:03, 30 dakikada bir | Yalnızca yeni aylık PDF bulunana kadar kontrol |

İlk başarılı yenilemeden sonra aynı günün sonraki workflow’larında kaynak PDF’si yerel cache’te bulunduğu için tekrar HTML/PDF isteği yapılmaz. Yeni PDF henüz yayımlanmamışsa ilgili yayın penceresindeki bir sonraki kontrol tekrar dener. Böylece geç yayımlanan Akademik Kulüp menüsü pazartesi günü, geç yayımlanan aylık menü ise ayın 1’i veya gerekiyorsa ilk uygun pazartesi günü yakalanır.

Çalışma sırası şöyledir:

1. `auto` modu hedef tarihe ve haftanın gününe bakarak `main`, `club` veya boş yenileme kümesini seçer.
2. Yenileme kümesi boşsa yalnızca `data/cache/pdfs` içindeki PDF’ler taranır; ESTÜ’ye ağ isteği yapılmaz.
3. Yenileme gerekiyorsa yalnızca ilgili HTML sayfası alınır ve yeni PDF bağlantısı aranır. Aynı PDF URL’si `data/cache` içinde bulunuyorsa PDF yeniden indirilmez.
4. Kaynak URL’leri `data/cache/source_state.json` içinde saklanır; bu dosya workflow commit’iyle korunur.
5. PDF içindeki FlateDecode akışları, `Tj`/`TJ` metin operatörleri ve `ToUnicode` CMap eşlemeleri Python standart kütüphanesiyle çözülür.
6. `data/menu.json` güncellenir ve yalnızca değişiklik varsa commit edilip repository’ye gönderilir.
7. Production değişikliğinde workflow ayrıca semver tag oluşturur; jsDelivr `@latest` adresi bu release’i kullanır.
8. Pages’e yalnızca `site/data/menu.json` ve küçük bir bilgilendirme sayfası yayımlanır; PDF cache dosyaları Pages’e yüklenmez.

Hafta sonu üretiminde ESTÜ isteği yapılmaz ve JSON `status: "weekend_closed"` üretir. Yeni PDF yayınlanmamışsa veya cache’te hedef gün bulunamıyorsa JSON `status: "not_published"` olur. Sadece bir yemekhane üretilebilirse `status: "partial"` yazılır. Ağ hatasında son geçerli cache korunur ve ilgili yayın günündeki sonraki kontrol yeniden dener.

## JSON alanları

| Alan | Açıklama |
|---|---|
| `status` | `ok`, `partial`, `not_published` veya `weekend_closed` |
| `date` | `GG.AA.YYYY` biçiminde hedef tarih |
| `isoDate` | `YYYY-MM-DD` biçiminde tarih |
| `weekday` | ISO hafta günü; Pazartesi `1`, Pazar `7` |
| `isWeekend` | Hafta sonu durumunu gösterir |
| `anaYemekhane` | Ana Yemekhane yemek adları dizisi |
| `akademikKulup` | Akademik Kulüp yemek adları dizisi |
| `generatedAt` | Üretim zamanı, Türkiye saat dilimiyle |
| `sources` | Kullanılan ESTÜ sayfa/PDF URL’leri ve cache bilgisi |
| `retrySchedule` | Menü hazır değilse planlanan tekrar kontrol saatleri |
| `errors` | Kısmi sonuç veya yayınlanmamış menü varsa hata/uyarı metinleri |

## Kestirmeler akışı

Kestirmeler’de **URL’nin İçeriğini Al** eylemine öncelikli raw GitHub adresini veya raw GitHub 429 döndürürse aşağıdaki güncel CDN JSON adresini verin:

```text
https://gcore.jsdelivr.net/gh/beytullahgol/estu-menu@latest/data/menu.json
```

Eylemin **Yöntem** seçeneği `GET`, **İstek Gövdesi** ise boş olmalıdır; `POST`, JSON gövdesi veya `menu2.php` adresi kullanılmamalıdır. Ardından **Sözlük Al** eylemini seçin. Ana yemekhane için `anaYemekhane`, Akademik Kulüp için `akademikKulup` anahtarlarından **Liste Al** ile diziyi alın. `status` değeri `weekend_closed` veya `not_published` ise doğrudan `message` alanını bildirim olarak gösterin. Raw GitHub ve jsDelivr hızlı statik dosya sunduğu için bu akış ESTÜ veya InfinityFree bot korumasına bağlı değildir. GitHub Pages yalnızca alternatif yayın adresidir.

## Manuel workflow testi

GitHub repository’sindeki **Actions** sekmesinden **ESTÜ menü verisini güncelle** workflow’unu açıp **Run workflow** seçilebilir. `date` alanına `13.08.2026` gibi bir tarih yazıldığında, canlı ESTÜ PDF’lerinden o tarih için regresyon testi yapılır. Test sonucu production `data/menu.json` dosyasını değiştirmez; ayrı bir raw JSON dosyasına yazılır:

```text
https://raw.githubusercontent.com/beytullahgol/estu-menu/main/data/test/13.08.2026.json
```

Başka bir tarih için dosya adındaki tarihi değiştirin. Örneğin `14.08.2026` testi için:

```text
https://raw.githubusercontent.com/beytullahgol/estu-menu/main/data/test/14.08.2026.json
```

Bu test URL’si Kestirmeler’de production URL’siyle aynı şekilde kullanılabilir: **URL → URL’nin İçeriğini Al (GET) → Sözlük Al**. Tarih testinin ardından iPhone’un production menüsüne dönmek için tekrar şu adresi kullanın:

```text
https://raw.githubusercontent.com/beytullahgol/estu-menu/main/data/menu.json
```

Alan boş bırakılırsa workflow Türkiye saatine göre güncel tarihi `data/menu.json` içine yayımlar ve Pages dağıtımını yapar. Manuel tarih testi artık artifact olarak da saklanır; Actions çalıştırmasının özetindeki **Artifacts** bölümünden indirilebilir.

## Kaynaklar

- Ana yemekhane: <https://saglikkulturspor.eskisehir.edu.tr/tr/Icerik/Detay/yemekhaneler>
- Akademik Kulüp: <https://saglikkulturspor.eskisehir.edu.tr/tr/Icerik/Detay/gunluk-menu>
