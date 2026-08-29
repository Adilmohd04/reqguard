# Invoice Payment State Requirements

REQ-07: Invoices marked as 'paid' or 'void' cannot accept additional payments. Applying payment to a finalized invoice must raise InvoiceClosedError.
