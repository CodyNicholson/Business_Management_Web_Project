import { useQuery } from "@tanstack/react-query";
import { Printer } from "lucide-react";
import { useParams } from "react-router-dom";
import { api, type Rec } from "../lib/api";
import { formatDate, formatMoney, formatQty } from "../lib/format";
import { useBootstrap } from "../lib/hooks";

type Layout = { title: (r: Rec) => string; partner: string; date: string; lines: string; columns: [string, string, "text" | "qty" | "money" | "m2o"][]; totals: [string, string][] };

const LAYOUTS: Record<string, Layout> = {
  invoice: {
    title: (r) => ({ out_invoice: "Invoice", out_refund: "Credit Note", in_invoice: "Vendor Bill", in_refund: "Vendor Refund", entry: "Journal Entry" }[r.move_type as string] ?? "Invoice"),
    partner: "partner_id", date: "date", lines: "invoice_line_ids", columns: [["name", "Description", "text"], ["quantity", "Quantity", "qty"], ["price_unit", "Unit Price", "money"], ["tax_id", "Tax", "m2o"], ["price_subtotal", "Amount", "money"]],
    totals: [["amount_untaxed", "Untaxed Amount"], ["amount_tax", "Tax"], ["amount_total", "Total"], ["amount_residual", "Amount Due"]],
  },
  sale: {
    title: (r) => (r.state === "sale" ? "Sales Order" : "Quotation"), partner: "partner_id", date: "date_order", lines: "order_line",
    columns: [["name", "Description", "text"], ["product_uom_qty", "Quantity", "qty"], ["price_unit", "Unit Price", "money"], ["discount", "Disc.%", "qty"], ["tax_id", "Tax", "m2o"], ["price_subtotal", "Amount", "money"]],
    totals: [["amount_untaxed", "Untaxed Amount"], ["amount_tax", "Taxes"], ["amount_total", "Total"]],
  },
  purchase: {
    title: (r) => (r.state === "purchase" ? "Purchase Order" : "Request for Quotation"), partner: "partner_id", date: "date_order", lines: "order_line",
    columns: [["name", "Description", "text"], ["product_qty", "Quantity", "qty"], ["price_unit", "Unit Price", "money"], ["tax_id", "Tax", "m2o"], ["price_subtotal", "Amount", "money"]],
    totals: [["amount_untaxed", "Untaxed Amount"], ["amount_tax", "Taxes"], ["amount_total", "Total"]],
  },
  picking: {
    title: (r) => ({ incoming: "Receipt", outgoing: "Delivery Slip", internal: "Internal Transfer" }[r.kind as string] ?? "Transfer"), partner: "partner_id", date: "scheduled_date", lines: "move_ids",
    columns: [["name", "Product", "text"], ["quantity", "Demand", "qty"], ["quantity_done", "Done", "qty"]], totals: [],
  },
};

export function PrintPage() {
  const { kind, model, id } = useParams();
  const { data: boot } = useBootstrap();
  const layout = LAYOUTS[kind ?? ""];
  const record = useQuery<Rec>({ queryKey: ["record", model, Number(id)], enabled: !!layout, queryFn: () => api.get(`/api/models/${model}/records/${id}`) });
  const partnerId = layout ? record.data?.[layout.partner]?.[0] : undefined;
  const partner = useQuery<Rec>({ queryKey: ["record", "res.partner", partnerId], enabled: !!partnerId, queryFn: () => api.get(`/api/models/res.partner/records/${partnerId}`) });
  if (!layout) return <div className="empty"><p>Unknown document.</p></div>;
  if (record.isLoading) return <div className="center-screen"><div className="spinner" role="status" aria-label="Loading" /></div>;
  if (record.error || !record.data) return <div className="empty"><p>{(record.error as Error)?.message}</p></div>;
  const r = record.data;
  const c = boot?.company ?? {};
  const value = (type: string, v: any) => (type === "money" ? formatMoney(v) : type === "qty" ? formatQty(v) : type === "m2o" ? v?.[1] ?? "" : v ?? "");
  return (
    <div className="print-page">
      <div className="no-print print-bar"><button className="btn btn-primary" onClick={() => window.print()}><Printer size={15} /> Print</button></div>
      <header className="print-head">
        <div><h2>{c.name}</h2><div>{c.street}</div><div>{[c.city, c.zip].filter(Boolean).join(" ")}</div><div>{c.country}</div><div>{c.tax_id && `Tax ID: ${c.tax_id}`}</div></div>
        <div className="print-partner">{partner.data && <><strong>{partner.data.name}</strong><div>{partner.data.street}</div><div>{[partner.data.city, partner.data.zip].filter(Boolean).join(" ")}</div><div>{partner.data.country}</div></>}</div>
      </header>
      <h1>{layout.title(r)} {r.name}</h1>
      <p>Date: {formatDate(r[layout.date])}{r.invoice_date_due ? ` · Due: ${formatDate(r.invoice_date_due)}` : ""}{r.payment_state === "paid" ? " · Paid" : ""}</p>
      <table className="data-table print-lines">
        <thead><tr>{layout.columns.map(([k, label, t]) => <th key={k} className={t === "qty" || t === "money" ? "num" : ""}>{label}</th>)}</tr></thead>
        <tbody>{(r[layout.lines] ?? []).map((line: Rec) => <tr key={line.id}>{layout.columns.map(([k, , t]) => <td key={k} className={t === "qty" || t === "money" ? "num" : ""}>{value(t, line[k])}</td>)}</tr>)}</tbody>
      </table>
      {layout.totals.length > 0 && <dl className="totals">{layout.totals.map(([k, label]) => <div key={k} className={k === "amount_total" ? "totals-final" : ""}><dt>{label}</dt><dd>{formatMoney(r[k])}</dd></div>)}</dl>}
      {r.narration && <p className="pre-wrap">{r.narration}</p>}
      {r.note && <p className="pre-wrap">{r.note}</p>}
    </div>
  );
}
