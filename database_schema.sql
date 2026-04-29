-- Neon_ai PostgreSQL schema
-- Source of truth: legacy Argon schema provided in project context.
-- Goal: runnable on an empty PostgreSQL / Supabase database.
-- Notes:
-- - Quoted PascalCase identifiers are preserved.
-- - Table order is arranged to satisfy foreign-key dependencies.
-- - No sample data is inserted.
-- - No DROP statements are included.

BEGIN;

CREATE SEQUENCE IF NOT EXISTS "Customer_CustomerID_seq";
CREATE SEQUENCE IF NOT EXISTS "Employee_EmployeeID_seq";
CREATE SEQUENCE IF NOT EXISTS "Material_ItemID_seq";
CREATE SEQUENCE IF NOT EXISTS "Task_TaskID_seq";
CREATE SEQUENCE IF NOT EXISTS "Vendor_VendorID_seq";
CREATE SEQUENCE IF NOT EXISTS "Site_SiteID_seq";
CREATE SEQUENCE IF NOT EXISTS "CustomerContact_CustomerContactID_seq";
CREATE SEQUENCE IF NOT EXISTS "VendorContact_VendorContactID_seq";
CREATE SEQUENCE IF NOT EXISTS "EstimateLabor_LaborID_seq";
CREATE SEQUENCE IF NOT EXISTS "EstimateMaterial_MaterialID_seq";
CREATE SEQUENCE IF NOT EXISTS "Invoice_CustomerInvoiceId_seq";
CREATE SEQUENCE IF NOT EXISTS "MaterialVendorPriceHistory_MaterialVendorPriceHistoryID_seq";
CREATE SEQUENCE IF NOT EXISTS "PriceRequest_PriceRequestID_seq";
CREATE SEQUENCE IF NOT EXISTS "PriceRequestItem_PRItemID_seq";
CREATE SEQUENCE IF NOT EXISTS "PriceRequestVendor_PRVendorID_seq";
CREATE SEQUENCE IF NOT EXISTS "WorkOrder_WorkOrderID_seq";
CREATE SEQUENCE IF NOT EXISTS "PurchaseOrder_PurchaseOrderID_seq";
CREATE SEQUENCE IF NOT EXISTS "PurchaseOrderItem_POItemID_seq";
CREATE SEQUENCE IF NOT EXISTS "Time_TimeID_seq";
CREATE SEQUENCE IF NOT EXISTS "VendorInvoice_VendorInvoiceID_seq";

CREATE TABLE IF NOT EXISTS public."Customer" (
    "CustomerID" integer NOT NULL DEFAULT nextval('"Customer_CustomerID_seq"'::regclass),
    "CustomerName" text,
    "Address" text,
    "StreetName" text,
    "CityName" text,
    "PostalCode" text,
    "Phone" text,
    "Email" text,
    CONSTRAINT "Customer_pkey" PRIMARY KEY ("CustomerID")
);

CREATE TABLE IF NOT EXISTS public."Employee" (
    "EmployeeID" integer NOT NULL DEFAULT nextval('"Employee_EmployeeID_seq"'::regclass),
    "EmployeeName" text,
    "EmployeeRate" numeric,
    "EmployeeAddress" text,
    "EmployeeCity" text,
    "EmployeePhone" text,
    "EmployeeEmail" text,
    "EmployeeBurden" numeric DEFAULT 30.00,
    "IsActive" boolean DEFAULT true,
    "EmployeeClass" text,
    CONSTRAINT "Employee_pkey" PRIMARY KEY ("EmployeeID")
);

CREATE TABLE IF NOT EXISTS public."Material" (
    "ItemID" integer NOT NULL DEFAULT nextval('"Material_ItemID_seq"'::regclass),
    "PartNumber" text NOT NULL,
    "Description" text NOT NULL,
    "Unit" text,
    "InternalPrice" numeric DEFAULT 0.00,
    "NedcoPrice" numeric,
    "NedcoLastPriceDate" timestamp with time zone,
    "NedcoLastRFQ" text,
    "NedcoPartNumber" text,
    "GescanPrice" numeric,
    "GescanLastPriceDate" timestamp with time zone,
    "GescanLastRFQ" text,
    "GescanPartNumber" text,
    "EecolPrice" numeric,
    "EecolLastPriceDate" timestamp with time zone,
    "EecolLastRFQ" text,
    "EecolPartNumber" text,
    "GuillevinPrice" numeric,
    "GuillevinLastPriceDate" timestamp with time zone,
    "GuillevinLastRFQ" text,
    "GuillevinPartNumber" text,
    "CarryPriceSource" text NOT NULL DEFAULT 'Internal'::text,
    "IsActive" boolean NOT NULL DEFAULT true,
    CONSTRAINT "Material_pkey" PRIMARY KEY ("ItemID"),
    CONSTRAINT "Material_PartNumber_key" UNIQUE ("PartNumber")
);

CREATE TABLE IF NOT EXISTS public."StandardRole" (
    "RoleID" integer GENERATED ALWAYS AS IDENTITY NOT NULL,
    "RoleName" text NOT NULL,
    "BaseRate" numeric DEFAULT 0.00,
    "BurdenPercent" numeric DEFAULT 30.00,
    CONSTRAINT "StandardRole_pkey" PRIMARY KEY ("RoleID"),
    CONSTRAINT "StandardRole_RoleName_key" UNIQUE ("RoleName")
);

CREATE TABLE IF NOT EXISTS public."Task" (
    "TaskID" integer NOT NULL DEFAULT nextval('"Task_TaskID_seq"'::regclass),
    "TaskName" text NOT NULL,
    CONSTRAINT "Task_pkey" PRIMARY KEY ("TaskID"),
    CONSTRAINT "Task_TaskName_key" UNIQUE ("TaskName")
);

CREATE TABLE IF NOT EXISTS public."Vendor" (
    "VendorID" integer NOT NULL DEFAULT nextval('"Vendor_VendorID_seq"'::regclass),
    "AccountNumber" integer,
    "VendorName" text,
    "VendorAddressNumber" text,
    "VendorCity" text,
    "VendorContactName" text,
    "VendorContactNumber" text,
    "BillingAddress" text,
    "BillingCity" text,
    "VendorMainPhone" text,
    CONSTRAINT "Vendor_pkey" PRIMARY KEY ("VendorID")
);

CREATE TABLE IF NOT EXISTS public."Site" (
    "SiteID" integer NOT NULL DEFAULT nextval('"Site_SiteID_seq"'::regclass),
    "CustomerID" integer,
    "SiteName" text,
    "StreetNumber" text,
    "StreetName" text,
    "City" text,
    CONSTRAINT "Site_pkey" PRIMARY KEY ("SiteID"),
    CONSTRAINT "Site_CustomerID_fkey"
        FOREIGN KEY ("CustomerID") REFERENCES public."Customer"("CustomerID")
);

CREATE TABLE IF NOT EXISTS public."CustomerContact" (
    "CustomerContactID" integer NOT NULL DEFAULT nextval('"CustomerContact_CustomerContactID_seq"'::regclass),
    "CustomerID" integer NOT NULL,
    "ContactName" text,
    "Phone" text,
    "Email" text,
    CONSTRAINT "CustomerContact_pkey" PRIMARY KEY ("CustomerContactID"),
    CONSTRAINT "CustomerContact_CustomerID_fkey"
        FOREIGN KEY ("CustomerID") REFERENCES public."Customer"("CustomerID")
);

CREATE TABLE IF NOT EXISTS public."VendorContact" (
    "VendorContactID" integer NOT NULL DEFAULT nextval('"VendorContact_VendorContactID_seq"'::regclass),
    "VendorID" integer NOT NULL,
    "ContactName" text,
    "Phone" text,
    "MobilePhone" text,
    "Email" text,
    CONSTRAINT "VendorContact_pkey" PRIMARY KEY ("VendorContactID"),
    CONSTRAINT "VendorContact_VendorID_fkey"
        FOREIGN KEY ("VendorID") REFERENCES public."Vendor"("VendorID")
);

CREATE TABLE IF NOT EXISTS public."Estimate" (
    "EstimateID" integer GENERATED ALWAYS AS IDENTITY NOT NULL,
    "CreatedDate" date NOT NULL DEFAULT CURRENT_DATE,
    "Status" character varying DEFAULT 'DRAFT'::character varying,
    "SubmitDate" date,
    "SiteID" integer,
    "Description" text,
    "IsConverted" boolean DEFAULT false,
    "BillingType" text DEFAULT 'Time and Materials'::text,
    "LaborMarkUp" numeric DEFAULT 0,
    "MaterialMarkUp" numeric DEFAULT 0,
    "TotalAmount" numeric DEFAULT 0.00,
    CONSTRAINT "Estimate_pkey" PRIMARY KEY ("EstimateID"),
    CONSTRAINT "Estimate_SiteID_fkey"
        FOREIGN KEY ("SiteID") REFERENCES public."Site"("SiteID")
);

CREATE TABLE IF NOT EXISTS public."EstimateLabor" (
    "LaborID" integer NOT NULL DEFAULT nextval('"EstimateLabor_LaborID_seq"'::regclass),
    "EstimateID" integer,
    "RoleDescription" text,
    "Hours" numeric DEFAULT 0.00,
    "Rate" numeric DEFAULT 0.00,
    "LineTotal" numeric DEFAULT 0.00,
    "RoleID" integer,
    CONSTRAINT "EstimateLabor_pkey" PRIMARY KEY ("LaborID"),
    CONSTRAINT "EstimateLabor_EstimateID_fkey"
        FOREIGN KEY ("EstimateID") REFERENCES public."Estimate"("EstimateID")
);

CREATE TABLE IF NOT EXISTS public."EstimateMaterial" (
    "EstimateMaterialID" integer NOT NULL DEFAULT nextval('"EstimateMaterial_MaterialID_seq"'::regclass),
    "EstimateID" integer,
    "Description" text,
    "Quantity" numeric DEFAULT 0.00,
    "UnitCost" numeric DEFAULT 0.00,
    "LineTotal" numeric DEFAULT 0.00,
    "IsOrdered" boolean DEFAULT false,
    "AwardedUnitCost" numeric,
    "AwardedVendor" text,
    "ItemID" integer,
    "PartNumber" text,
    "PriceSource" text,
    "IsCommitted" boolean NOT NULL DEFAULT false,
    "CommittedUnitCost" numeric,
    "CommittedLineTotal" numeric,
    CONSTRAINT "EstimateMaterial_pkey" PRIMARY KEY ("EstimateMaterialID"),
    CONSTRAINT "EstimateMaterial_EstimateID_fkey"
        FOREIGN KEY ("EstimateID") REFERENCES public."Estimate"("EstimateID")
);

CREATE TABLE IF NOT EXISTS public."Note" (
    "NoteID" bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
    "EstimateID" integer NOT NULL,
    "NoteText" text NOT NULL,
    "Timestamp" timestamp with time zone DEFAULT now(),
    "Category" text DEFAULT 'General'::text,
    CONSTRAINT "Note_pkey" PRIMARY KEY ("NoteID"),
    CONSTRAINT "note_estimateid_fkey"
        FOREIGN KEY ("EstimateID") REFERENCES public."Estimate"("EstimateID")
);

CREATE TABLE IF NOT EXISTS public."WorkOrder" (
    "WorkOrderID" integer NOT NULL DEFAULT nextval('"WorkOrder_WorkOrderID_seq"'::regclass),
    "SiteID" integer,
    "CreatedDate" text,
    "JobStatus" text,
    "Description" text,
    "BillingType" text,
    "IsApproved" boolean DEFAULT false,
    "IsClosed" boolean DEFAULT false,
    "CustomerPO" character varying,
    "AcceptanceDocPath" text,
    "SourceEstimateID" integer,
    CONSTRAINT "WorkOrder_pkey" PRIMARY KEY ("WorkOrderID"),
    CONSTRAINT "WorkOrder_SiteID_fkey"
        FOREIGN KEY ("SiteID") REFERENCES public."Site"("SiteID"),
    CONSTRAINT "WorkOrder_SourceEstimateID_fkey"
        FOREIGN KEY ("SourceEstimateID") REFERENCES public."Estimate"("EstimateID")
);

CREATE TABLE IF NOT EXISTS public."Invoice" (
    "CustomerInvoiceId" integer NOT NULL DEFAULT nextval('"Invoice_CustomerInvoiceId_seq"'::regclass),
    "CustomerInvoiceDate" text,
    "WorkOrderID" integer,
    "CustomerInvoiceAmount" numeric,
    "InvDatePaid" text,
    "InvoiceStatus" text,
    "BillingMode" text,
    "LaborPercent" numeric DEFAULT 0,
    "MaterialPercent" numeric DEFAULT 0,
    "LaborMilestoneNote" text,
    "MaterialMilestoneNote" text,
    "ScopeOfWork" text,
    "CustomerInvoiceDocPath" text,
    "CustomerInvoiceSentAt" timestamp with time zone,
    CONSTRAINT "Invoice_pkey" PRIMARY KEY ("CustomerInvoiceId"),
    CONSTRAINT "Invoice_WorkOrderID_fkey"
        FOREIGN KEY ("WorkOrderID") REFERENCES public."WorkOrder"("WorkOrderID")
);

CREATE TABLE IF NOT EXISTS public."PriceRequest" (
    "PriceRequestID" integer NOT NULL DEFAULT nextval('"PriceRequest_PriceRequestID_seq"'::regclass),
    "EstimateID" integer,
    "DateSent" date DEFAULT CURRENT_DATE,
    "DueDate" date,
    "Notes" text,
    "Status" text DEFAULT 'Pending'::text,
    "VendorID" integer,
    "VendorQuoteNumber" text,
    "VendorQuoteDate" date,
    "QuoteFilePath" text,
    CONSTRAINT "PriceRequest_pkey" PRIMARY KEY ("PriceRequestID"),
    CONSTRAINT "pricerequest_estimateid_fkey"
        FOREIGN KEY ("EstimateID") REFERENCES public."Estimate"("EstimateID"),
    CONSTRAINT "fk_vendor"
        FOREIGN KEY ("VendorID") REFERENCES public."Vendor"("VendorID")
);

CREATE TABLE IF NOT EXISTS public."PriceRequestVendor" (
    "PRVendorID" integer NOT NULL DEFAULT nextval('"PriceRequestVendor_PRVendorID_seq"'::regclass),
    "PriceRequestID" integer NOT NULL,
    "VendorID" integer NOT NULL,
    "QuoteReceivedDate" date,
    "QuoteTotal" numeric,
    "IsAwarded" boolean DEFAULT false,
    CONSTRAINT "PriceRequestVendor_pkey" PRIMARY KEY ("PRVendorID"),
    CONSTRAINT "prvendor_pr_fkey"
        FOREIGN KEY ("PriceRequestID") REFERENCES public."PriceRequest"("PriceRequestID"),
    CONSTRAINT "prvendor_vendor_fkey"
        FOREIGN KEY ("VendorID") REFERENCES public."Vendor"("VendorID")
);

CREATE TABLE IF NOT EXISTS public."PurchaseOrder" (
    "PurchaseOrderID" integer NOT NULL DEFAULT nextval('"PurchaseOrder_PurchaseOrderID_seq"'::regclass),
    "WorkOrderID" integer,
    "VendorID" integer,
    "EmployeeID" integer,
    "PurchaseOrderTotal" numeric,
    "Description" text,
    "Date" date,
    "PackingSlipNumber" text,
    "ShippingDocuments" text,
    "RecievedDate" integer,
    "VendorQuoteNumber" text,
    "VendorQuoteDate" date,
    "Status" text DEFAULT 'Draft'::text,
    "ExpectedArrivalDate" date,
    "ExpectedArrivalNote" text,
    CONSTRAINT "PurchaseOrder_pkey" PRIMARY KEY ("PurchaseOrderID"),
    CONSTRAINT "PurchaseOrder_WorkOrderID_fkey"
        FOREIGN KEY ("WorkOrderID") REFERENCES public."WorkOrder"("WorkOrderID"),
    CONSTRAINT "PurchaseOrder_VendorID_fkey"
        FOREIGN KEY ("VendorID") REFERENCES public."Vendor"("VendorID"),
    CONSTRAINT "PurchaseOrder_EmployeeID_fkey"
        FOREIGN KEY ("EmployeeID") REFERENCES public."Employee"("EmployeeID")
);

CREATE TABLE IF NOT EXISTS public."PriceRequestItem" (
    "PRItemID" integer NOT NULL DEFAULT nextval('"PriceRequestItem_PRItemID_seq"'::regclass),
    "PriceRequestID" integer NOT NULL,
    "MaterialID" integer NOT NULL,
    "QuantityOverride" numeric,
    "QuotedUnitPrice" numeric,
    "IsSubstitute" boolean DEFAULT false,
    "SubstituteNotes" text,
    "IsCarried" boolean DEFAULT false,
    CONSTRAINT "PriceRequestItem_pkey" PRIMARY KEY ("PRItemID"),
    CONSTRAINT "pritem_pr_fkey"
        FOREIGN KEY ("PriceRequestID") REFERENCES public."PriceRequest"("PriceRequestID"),
    CONSTRAINT "pritem_material_fkey"
        FOREIGN KEY ("MaterialID") REFERENCES public."EstimateMaterial"("EstimateMaterialID")
);

CREATE TABLE IF NOT EXISTS public."PurchaseOrderItem" (
    "POItemID" integer NOT NULL DEFAULT nextval('"PurchaseOrderItem_POItemID_seq"'::regclass),
    "PurchaseOrderID" integer,
    "MaterialID" integer,
    "QuantityOrdered" numeric,
    "UnitPriceAtOrder" numeric,
    "LineTotal" numeric,
    "QuantityReceived" numeric DEFAULT 0.00,
    "Description" text,
    "LastReceivedDate" text,
    "LastPackingSlip" text,
    "CustomerInvoiceId" integer,
    "CatalogItemID" integer,
    CONSTRAINT "PurchaseOrderItem_pkey" PRIMARY KEY ("POItemID"),
    CONSTRAINT "PurchaseOrderItem_PurchaseOrderID_fkey"
        FOREIGN KEY ("PurchaseOrderID") REFERENCES public."PurchaseOrder"("PurchaseOrderID"),
    CONSTRAINT "PurchaseOrderItem_MaterialID_fkey"
        FOREIGN KEY ("MaterialID") REFERENCES public."EstimateMaterial"("EstimateMaterialID")
);

CREATE TABLE IF NOT EXISTS public."PurchaseOrderReceipt" (
    "ReceiptID" integer GENERATED ALWAYS AS IDENTITY NOT NULL,
    "PurchaseOrderID" integer NOT NULL,
    "DocumentType" text,
    "DocumentRef" text NOT NULL,
    "ReceiveDate" date,
    "CreatedAt" timestamp with time zone DEFAULT now(),
    CONSTRAINT "PurchaseOrderReceipt_pkey" PRIMARY KEY ("ReceiptID")
);

CREATE TABLE IF NOT EXISTS public."PurchaseOrderReceiptItem" (
    "ReceiptItemID" integer GENERATED ALWAYS AS IDENTITY NOT NULL,
    "ReceiptID" integer NOT NULL,
    "POItemID" integer NOT NULL,
    "QuantityReceived" numeric DEFAULT 0,
    CONSTRAINT "PurchaseOrderReceiptItem_pkey" PRIMARY KEY ("ReceiptItemID")
);

CREATE TABLE IF NOT EXISTS public."Time" (
    "TimeID" integer NOT NULL DEFAULT nextval('"Time_TimeID_seq"'::regclass),
    "WorkOrderID" integer,
    "TaskId" text NOT NULL,
    "StartTime" numeric,
    "EndTime" numeric,
    "HoursWorked" numeric,
    "DateWorked" date,
    "WorkerID" integer,
    "EntrySource" text DEFAULT 'App'::text,
    "HourlyRate" numeric DEFAULT 0.00,
    "Status" text DEFAULT 'Pending'::text,
    "CustomerInvoiceId" integer,
    CONSTRAINT "Time_pkey" PRIMARY KEY ("TimeID"),
    CONSTRAINT "Time_WorkOrderID_fkey"
        FOREIGN KEY ("WorkOrderID") REFERENCES public."WorkOrder"("WorkOrderID"),
    CONSTRAINT "Time_WorkerID_fkey"
        FOREIGN KEY ("WorkerID") REFERENCES public."Employee"("EmployeeID")
);

CREATE TABLE IF NOT EXISTS public."VendorInvoice" (
    "VendorInvoiceID" integer NOT NULL DEFAULT nextval('"VendorInvoice_VendorInvoiceID_seq"'::regclass),
    "VendorInvoiceNumber" text NOT NULL,
    "PurchaseOrderID" integer NOT NULL,
    "VendorInvoiceDate" text,
    "VendorInvoiceDueDate" text,
    "VendorInvoiceAmount" numeric,
    "VendorInvoiceStatus" text,
    "Description" text,
    "VendorInvoicePaidDate" date,
    CONSTRAINT "VendorInvoice_pkey" PRIMARY KEY ("VendorInvoiceID"),
    CONSTRAINT "VendorInvoice_PurchaseOrderID_fkey"
        FOREIGN KEY ("PurchaseOrderID") REFERENCES public."PurchaseOrder"("PurchaseOrderID")
);

CREATE TABLE IF NOT EXISTS public."MaterialVendorPriceHistory" (
    "MaterialVendorPriceHistoryID" integer NOT NULL DEFAULT nextval('"MaterialVendorPriceHistory_MaterialVendorPriceHistoryID_seq"'::regclass),
    "ItemID" integer NOT NULL,
    "VendorID" integer,
    "VendorName" text,
    "WholesalerName" text,
    "PriceRequestID" integer,
    "PRItemID" integer,
    "VendorPartNumber" text,
    "VendorQuoteNumber" text,
    "QuoteDate" date,
    "UnitPrice" numeric NOT NULL,
    "SourceType" text,
    "SourceFilePath" text,
    "CapturedAt" timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT "MaterialVendorPriceHistory_pkey" PRIMARY KEY ("MaterialVendorPriceHistoryID"),
    CONSTRAINT "MaterialVendorPriceHistory_ItemID_fkey"
        FOREIGN KEY ("ItemID") REFERENCES public."Material"("ItemID"),
    CONSTRAINT "MaterialVendorPriceHistory_VendorID_fkey"
        FOREIGN KEY ("VendorID") REFERENCES public."Vendor"("VendorID"),
    CONSTRAINT "MaterialVendorPriceHistory_PriceRequestID_fkey"
        FOREIGN KEY ("PriceRequestID") REFERENCES public."PriceRequest"("PriceRequestID"),
    CONSTRAINT "MaterialVendorPriceHistory_PRItemID_fkey"
        FOREIGN KEY ("PRItemID") REFERENCES public."PriceRequestItem"("PRItemID")
);

ALTER TABLE public."PurchaseOrderReceipt"
    ADD CONSTRAINT "PurchaseOrderReceipt_PurchaseOrderID_fkey"
    FOREIGN KEY ("PurchaseOrderID") REFERENCES public."PurchaseOrder"("PurchaseOrderID");

ALTER TABLE public."PurchaseOrderReceiptItem"
    ADD CONSTRAINT "PurchaseOrderReceiptItem_ReceiptID_fkey"
    FOREIGN KEY ("ReceiptID") REFERENCES public."PurchaseOrderReceipt"("ReceiptID");

ALTER TABLE public."PurchaseOrderReceiptItem"
    ADD CONSTRAINT "PurchaseOrderReceiptItem_POItemID_fkey"
    FOREIGN KEY ("POItemID") REFERENCES public."PurchaseOrderItem"("POItemID");

COMMIT;
