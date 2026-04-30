from docx import Document
from docx.shared import Inches
from neon_ai.database.automation import get_project_file_paths
import os
import datetime
from neon_ai.database.invoices import get_customer_invoice_memory


def generate_invoice_docx(header, mode):
    """
    Creates the final customer-facing invoice.
    Saves to argon_ai/outbox.
    """
    doc = Document()

    # 1. Header Information
    # Using .get() as a failsafe in case the dict doesn't have the exact key yet
    inv_id = header.get("CustomerInvoiceId", "DRAFT")
    doc.add_heading(f"INVOICE: {inv_id}", 0)

    p = doc.add_paragraph()
    p.add_run(f"Date: {datetime.date.today()}\n").italic = True
    p.add_run(f"Customer: {header.get('CustomerName', 'Unknown')}\n").bold = True
    p.add_run(f"Site: {header.get('SiteAddress', 'Unknown')}\n")
    p.add_run(f"Work Order: {header.get('WorkOrderID', 'Unknown')}")

    # 2. The Living Scope
    doc.add_heading("Scope of Work & Project Narrative", level=1)
    doc.add_paragraph(header.get("ScopeOfWork", header.get("Scope", "No scope provided.")))

    # 3. Billing Details
    doc.add_heading("Billing Summary", level=1)
    invoice_memory = get_customer_invoice_memory(inv_id) if inv_id != "DRAFT" else {}

    if mode == "T&M":
        doc.add_paragraph("Billing Basis: Time & Materials (Itemized)")
        labor_lines = invoice_memory.get("labor_lines", [])
        material_lines = invoice_memory.get("material_lines", [])

        if labor_lines:
            doc.add_paragraph("Labor")
            labor_table = doc.add_table(rows=1, cols=4)
            labor_table.style = "Table Grid"
            for cell, text in zip(labor_table.rows[0].cells, ["Date / Description", "Hours", "Rate", "Total"]):
                cell.text = text
            for row in labor_lines:
                cells = labor_table.add_row().cells
                cells[0].text = f"{row.get('DateWorked')} | {row.get('EmployeeName')} | {row.get('TaskName') or ''}"
                cells[1].text = f"{float(row.get('HoursWorked') or 0):.2f}"
                cells[2].text = f"${float(row.get('HourlyRate') or 0):,.2f}"
                cells[3].text = f"${float(row.get('LineTotal') or 0):,.2f}"

        if material_lines:
            doc.add_paragraph("Materials")
            mat_table = doc.add_table(rows=1, cols=4)
            mat_table.style = "Table Grid"
            for cell, text in zip(mat_table.rows[0].cells, ["PO / Description", "Qty", "Unit Cost", "Total"]):
                cell.text = text
            for row in material_lines:
                cells = mat_table.add_row().cells
                cells[0].text = f"PO #{row.get('PurchaseOrderID')} | {row.get('Description') or ''}"
                cells[1].text = f"{float(row.get('Qty') or 0):.2f}"
                cells[2].text = f"${float(row.get('Cost') or 0):,.2f}"
                cells[3].text = f"${float(row.get('LineTotal') or 0):,.2f}"
    else:
        # Stipulated Price Breakdown
        doc.add_paragraph("Billing Basis: Stipulated Price / Progress Claim")
        table = doc.add_table(rows=1, cols=3)
        table.style = "Table Grid"
        hdr = table.rows[0].cells
        hdr[0].text = "Component"
        hdr[1].text = "Progress %"
        hdr[2].text = "Amount Billed"

        est_total = float(header.get("EstimateTotal") or 0.0)

        # Labor Row
        l_perc = float(header.get("LaborPercent") or 0.0)
        l_row = table.add_row().cells
        l_row[0].text = f"Labor Milestone: {header.get('LaborMilestoneNote', '')}"
        l_row[1].text = f"{l_perc}%"
        l_row[2].text = f"${(est_total * l_perc / 100.0):.2f}"

        # Material Row
        m_perc = float(header.get("MaterialPercent") or 0.0)
        m_row = table.add_row().cells
        m_row[0].text = f"Material Milestone: {header.get('MaterialMilestoneNote', '')}"
        m_row[1].text = f"{m_perc}%"
        m_row[2].text = f"${(est_total * m_perc / 100.0):.2f}"

    # Grand Total
    total_amount = float(header.get("CustomerInvoiceAmount") or 0.0)
    doc.add_paragraph(f"\nGRAND TOTAL DUE: ${total_amount:,.2f}").bold = True

    # 4. Save to Outbox
    est_id = header.get("EstimateID", "0000")
    cust_name = header.get("CustomerName", "Customer")
    site_addr = header.get("SiteAddress", "Unknown")

    # Get the official project folder using the Golden Rule
    folder_path, _ = get_project_file_paths(cust_name, site_addr, est_id)

    # Ensure the project folder exists
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)

    # Define the Invoice filename specifically
    # (We don't use the doc_path from the function because that points to the Estimate)
    inv_filename = f"Invoice_{inv_id}_{cust_name.replace(' ', '_')}.docx"
    save_path = os.path.join(folder_path, inv_filename)

    doc.save(save_path)

    print(f"Invoice saved to project folder: {save_path}")
    return save_path
