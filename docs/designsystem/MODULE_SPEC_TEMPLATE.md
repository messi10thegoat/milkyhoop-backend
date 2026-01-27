# Module Specification Template

Template untuk menspesifikasikan modul baru di frontend MilkyHoop.
Gunakan template ini saat meminta AI membuat modul baru.

---

## Informasi Modul

```yaml
module_name: [Nama Modul]
module_id: [kebab-case identifier]
module_title_id: [Judul dalam Bahasa Indonesia]
category: [master-data | transaction | report | setting]
```

**Contoh:**
```yaml
module_name: Customer
module_id: customer
module_title_id: Pelanggan
category: master-data
```

---

## 1. List View Spec

### 1.1 Header

```yaml
header:
  title: [Judul halaman]
  back_action: [close_panel | navigate_back]
  primary_action:
    icon: [plus | scan | import | null]
    label: [Label tombol jika hover]
    action: [open_form | open_sheet | custom]
```

### 1.2 Search Bar

```yaml
search:
  placeholder: "Cari [object]..."
  has_filter_button: [true | false]
  filter_options:
    - key: [filter_key]
      label: [Label filter]
```

### 1.3 Stats Card (Optional)

```yaml
stats_card:
  enabled: [true | false]
  title: [Label summary, e.g., "Total Piutang"]
  show_period: [true | false]

  segments:
    - key: [segment_key]
      label: [Label]
      color: [success | warning | danger | info]
    - key: [segment_key_2]
      label: [Label]
      color: [...]

  # Whether clicking legend filters the list
  legend_filters: [true | false]
```

**Contoh Stats Card:**
```yaml
stats_card:
  enabled: true
  title: "Total Piutang"
  show_period: true

  segments:
    - key: paid
      label: Lunas
      color: success
    - key: partial
      label: Bayar sebagian
      color: info
    - key: unpaid
      label: Belum dibayar
      color: warning
    - key: overdue
      label: Jatuh tempo
      color: danger

  legend_filters: true
```

### 1.4 List Item

```yaml
list_item:
  style: [accordion | card | simple_row]

  # Collapsed view
  collapsed:
    left_indicator: [status_dot | avatar | icon | none]
    primary_text: [field_name]
    secondary_text: [field_name | null]
    right_primary: [field_name]  # Amount / Value
    right_secondary: [field_name | status_label]
    has_chevron: [true | false]

  # Expanded view (for accordion style)
  expanded:
    detail_grid:
      - label: [Label]
        field: [field_name]
      - label: [Label]
        field: [field_name]

    badges:
      - field: [field_name]
        type: [status | tag | icon_tag]

    actions:
      - key: [action_key]
        label: [Label]
        style: [secondary | danger]
        icon: [icon_name | null]
```

**Contoh List Item (Sales Invoice):**
```yaml
list_item:
  style: accordion

  collapsed:
    left_indicator: status_dot
    primary_text: customer_name
    secondary_text: invoice_number
    right_primary: total_amount
    right_secondary: status_label
    has_chevron: true

  expanded:
    detail_grid:
      - label: Tanggal faktur
        field: invoice_date
      - label: Jatuh tempo
        field: due_date
      - label: Sudah dibayar
        field: paid_amount
      - label: Sisa tagihan
        field: remaining_amount

    badges:
      - field: doc_status
        type: tag
      - field: has_receipt
        type: icon_tag

    actions:
      - key: view_detail
        label: Lihat detail
        style: secondary
      - key: collect_payment
        label: Terima pembayaran
        style: secondary
```

### 1.5 Empty State

```yaml
empty_state:
  icon: [document | box | user | building | money]
  title: "Belum ada [object]"
  description: "[Object] yang Anda [action] akan muncul di sini"

  actions:
    - key: create
      label: [Label tombol]
      style: primary
      icon: [icon_name]
```

### 1.6 Pagination / Infinite Scroll

```yaml
pagination:
  type: [infinite_scroll | load_more_button | pagination]
  items_per_page: [10 | 20 | 50]
```

---

## 2. Create/Edit Form Spec

### 2.1 Form Header

```yaml
form_header:
  title_create: [Judul untuk create]
  title_edit: [Judul untuk edit]
  back_action: [confirm_discard | close_directly]
  save_button:
    label: [Simpan | Buat | etc]
    disabled_when: [form_invalid | empty_required]
```

### 2.2 Form Sections

```yaml
sections:
  - title: [Section Title | "Wajib Diisi" | "Opsional"]
    divider_before: [true | false]
    fields:
      - key: [field_key]
        type: [FieldPill | ToggleField | ItemsSection | TaxSelector]
        label: [Label]
        placeholder: [Placeholder]
        icon: [icon_name]
        required: [true | false]
        sheet_type: [selection | input | date | textarea | custom]
```

**Field Types:**

| Type | Description | Sheet Type |
|------|-------------|------------|
| `FieldPill` | Expandable pill dengan sheet | selection, input, date, textarea |
| `ToggleField` | Toggle switch | - |
| `TaxSelector` | Segmented button untuk tax rate | - |
| `ItemsSection` | List of line items | item_form |

**Contoh Form Sections:**
```yaml
sections:
  - title: "Wajib Diisi"
    fields:
      - key: customer
        type: FieldPill
        label: Pelanggan
        placeholder: Pilih pelanggan
        icon: user
        required: true
        sheet_type: selection

      - key: invoice_date
        type: FieldPill
        label: Tanggal Faktur
        placeholder: Pilih tanggal
        icon: calendar
        required: true
        sheet_type: date

      - key: due_date
        type: FieldPill
        label: Jatuh Tempo
        placeholder: Pilih tanggal
        icon: clock
        required: true
        sheet_type: date

      - key: items
        type: ItemsSection
        label: Item
        required: true

  - title: "Opsional"
    divider_before: true
    fields:
      - key: tax_inclusive
        type: ToggleField
        label: Harga Termasuk Pajak
        icon: receipt

      - key: tax_rate
        type: TaxSelector
        options: [0, 11, 12]

      - key: invoice_no
        type: FieldPill
        label: No. Faktur
        placeholder: Masukkan nomor
        icon: document
        sheet_type: input

      - key: discount
        type: FieldPill
        label: Diskon
        placeholder: Tambah diskon
        icon: tag
        sheet_type: custom

      - key: notes
        type: FieldPill
        label: Catatan
        placeholder: Tambah catatan
        icon: note
        sheet_type: textarea
```

### 2.3 Items Section (Line Items)

```yaml
items_section:
  add_button_label: "+ Tambah item"

  item_card:
    primary_text: [field]
    detail_text: "{quantity} {unit} × Rp {price}"
    amount_text: "Rp {subtotal}"
    discount_text: "-{discount}%"

    actions: [edit, delete]

  item_form_fields:
    - key: product
      type: search_select
      label: Produk
      placeholder: Cari produk...

    - key: quantity
      type: number
      label: Jumlah
      default: 1

    - key: unit
      type: text
      label: Satuan

    - key: price
      type: currency
      label: Harga

    - key: discount
      type: percent_or_amount
      label: Diskon
```

### 2.4 Summary Bar

```yaml
summary_bar:
  expandable: true
  position: fixed_bottom

  rows:
    - label: "Subtotal ({count} item)"
      field: subtotal
    - label: "Diskon"
      field: discount_amount
      style: negative
      editable: true
    - label: "PPN {rate}%"
      field: tax_amount
    - label: "Total"
      field: grand_total
      style: bold
```

### 2.5 Draft Management

```yaml
draft:
  auto_save: [true | false]
  storage_key: "draft_{module_id}"
  prompt_restore: true
```

---

## 3. Search Page Spec

```yaml
search_page:
  # Tampil sebagai full overlay
  style: full_overlay

  input:
    placeholder: "Cari [object]..."
    auto_focus: true

  filters:
    position: [below_input | chip_row]
    options:
      - key: [filter_key]
        label: [Label]

  results:
    style: [simple_list | card_list]
    highlight_match: true

  recent_searches:
    enabled: [true | false]
    max_items: 5
```

---

## 4. Sort & Filter Spec

### 4.1 Filter Modal

```yaml
filter_modal:
  style: bottom_sheet
  title: Filter

  options:
    - key: all
      label: Semua
    - key: [status_1]
      label: [Label]
    - key: [status_2]
      label: [Label]
```

### 4.2 Sort Modal

```yaml
sort_modal:
  style: bottom_sheet
  title: Urutkan

  options:
    - key: [field]
      label: [Label]
      default_order: [asc | desc]
```

---

## 5. API Endpoints

```yaml
api:
  base_path: /api/[resource]

  endpoints:
    list:
      method: GET
      path: /
      params:
        - limit: number
        - skip: number
        - sort: string
        - order: asc | desc
        - status: string (optional)
        - search: string (optional)

    summary:
      method: GET
      path: /summary

    detail:
      method: GET
      path: /{id}

    create:
      method: POST
      path: /

    update:
      method: PUT
      path: /{id}

    delete:
      method: DELETE
      path: /{id}
```

---

## 6. Data Types

```yaml
types:
  # List item type
  list_item:
    id: string
    [field]: [type]
    ...

  # Form data type
  form_data:
    [field]: [type]
    items: ItemData[]
    ...

  # Summary type
  summary:
    total: number
    [breakdown_key]: number
    ...
```

---

## Full Example: Expenses Module

```yaml
module_name: Expenses
module_id: expenses
module_title_id: Biaya & Pengeluaran
category: transaction

list_view:
  header:
    title: Biaya & Pengeluaran
    back_action: close_panel
    primary_action:
      icon: plus
      action: open_form

  search:
    placeholder: "Cari pengeluaran..."
    has_filter_button: true

  stats_card:
    enabled: true
    title: "Total Pengeluaran"
    show_period: true
    segments:
      - key: invoiced
        label: Sudah Difaktur
        color: success
      - key: billable
        label: Dapat Ditagih
        color: info
      - key: reimbursed
        label: Sudah Diganti
        color: warning
      - key: non-billable
        label: Tidak Dapat Ditagih
        color: danger
    legend_filters: true

  list_item:
    style: accordion
    collapsed:
      left_indicator: status_dot
      primary_text: title
      secondary_text: vendor
      right_primary: amount
      right_secondary: status_label
      has_chevron: true
    expanded:
      detail_grid:
        - label: Tanggal
          field: date
        - label: Akun
          field: account
        - label: Bayar dari
          field: paid_through
      badges:
        - field: has_receipt
          type: icon_tag
      actions:
        - key: view_detail
          label: Lihat detail
          style: secondary
        - key: create_invoice
          label: Buat faktur
          style: secondary

  empty_state:
    icon: money
    title: "Belum ada pengeluaran"
    description: "Pengeluaran yang Anda catat akan muncul di sini"

  pagination:
    type: infinite_scroll
    items_per_page: 10

form:
  form_header:
    title_create: Catat Pengeluaran
    title_edit: Edit Pengeluaran
    save_button:
      label: Simpan

  sections:
    - title: "Wajib Diisi"
      fields:
        - key: account
          type: FieldPill
          label: Akun Biaya
          placeholder: Pilih akun
          icon: folder
          required: true
          sheet_type: selection

        - key: date
          type: FieldPill
          label: Tanggal
          placeholder: Pilih tanggal
          icon: calendar
          required: true
          sheet_type: date

        - key: amount
          type: FieldPill
          label: Jumlah
          placeholder: Masukkan jumlah
          icon: money
          required: true
          sheet_type: currency

        - key: paid_through
          type: FieldPill
          label: Bayar dari
          placeholder: Pilih akun
          icon: wallet
          required: true
          sheet_type: selection

    - title: "Opsional"
      divider_before: true
      fields:
        - key: vendor
          type: FieldPill
          label: Vendor
          placeholder: Pilih vendor
          icon: building
          sheet_type: selection

        - key: customer
          type: FieldPill
          label: Pelanggan
          placeholder: Pilih pelanggan
          icon: user
          sheet_type: selection

        - key: billable
          type: ToggleField
          label: Dapat Ditagih
          icon: receipt

        - key: notes
          type: FieldPill
          label: Catatan
          placeholder: Tambah catatan
          icon: note
          sheet_type: textarea

        - key: attachments
          type: FieldPill
          label: Lampiran
          placeholder: Tambah file
          icon: paperclip
          sheet_type: file

api:
  base_path: /api/expenses
  endpoints:
    list:
      method: GET
      path: /
    summary:
      method: GET
      path: /summary
    create:
      method: POST
      path: /
```

---

## Notes for AI Implementation

1. **Gunakan design tokens** dari COMPONENT_LIBRARY.md
2. **Follow existing patterns** dari modul yang sudah ada
3. **Inline styles** dengan objek JavaScript, bukan className
4. **Framer Motion** untuk animasi panel dan sheet
5. **Mobile-first** dengan max-width 430px
6. **Indonesian language** untuk semua label dan placeholder
