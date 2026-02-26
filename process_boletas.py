#!/usr/bin/env python3

import re
import json
import datetime
import pdfplumber
from pathlib import Path
from pypdf import PdfReader, PdfWriter


def clear_directory(folder: Path, keep_gitkeep: bool = False) -> None:
    """Remove all files inside folder (not the folder itself)."""
    if not folder.exists():
        return
    for f in folder.iterdir():
        if f.is_file():
            if keep_gitkeep and f.name == ".gitkeep":
                continue
            f.unlink()


def extract_pdf_text(pdf_path, page_num):
    """Extract text from a specific page of a PDF using pdfplumber"""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            # Check if page number is valid
            if page_num > len(pdf.pages) or page_num < 1:
                print(f"Page {page_num} is out of range (PDF has {len(pdf.pages)} pages)")
                return ""

            # Get the page (0-indexed)
            page = pdf.pages[page_num - 1]

            # Extract text
            text = page.extract_text()

            if text:
                return text
            else:
                print(f"No text found on page {page_num}")
                return ""

    except Exception as e:
        print(f"Error extracting page {page_num} from {pdf_path}: {e}")
        return ""


def get_pdf_page_count(pdf_path):
    """Get the total number of pages in a PDF"""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            return len(pdf.pages)
    except Exception as e:
        print(f"Error getting page count for {pdf_path}: {e}")
        return 0


def extract_amounts_from_text(text):
    """Extract all amounts after $ symbols from text"""
    amounts = []

    try:
        # Find all amounts that come after $ (including commas and decimal points)
        # This pattern matches: $ followed by digits, commas, and decimal points
        amount_pattern = r'\$([0-9,]+\.[0-9]{2})'
        matches = re.findall(amount_pattern, text)

        # Remove commas for numeric value but keep original format for display
        for match in matches:
            clean_amount = match.replace(',', '')
            amounts.append({
                'formatted': match,
                'numeric': clean_amount,
                'raw_match': f'${match}'
            })
    except Exception as e:
        print(f"Error extracting amounts from text: {e}")

    return amounts


def extract_socio_numbers_from_text(text):
    """Extract all N° Socio numbers from text"""
    socio_numbers = []

    try:
        # Pattern: "N° Socio:" followed by 1-4 digits
        socio_pattern = r'N° Socio:\s*(\d{1,4})'
        matches = re.findall(socio_pattern, text)

        # Use only the first match (since all should be the same)
        if matches:
            first_match = matches[0]
            socio_numbers.append({
                'socio_number': first_match
            })
    except Exception as e:
        print(f"Error extracting socio numbers from text: {e}")

    return socio_numbers


def process_pdf(pdf_path, sector_name, output_folder):
    """Process a single PDF file and extract amounts and N° Socio numbers"""
    results = []

    try:
        # Get total number of pages
        total_pages = get_pdf_page_count(pdf_path)

        if total_pages == 0:
            print(f"Could not read {pdf_path}")
            return results

        print(f"Processing {sector_name}: {total_pages} pages total")

        # Process each page
        for page_num in range(1, total_pages + 1):
            print(f"  Processing page {page_num}...")

            # Extract text from page
            page_text = extract_pdf_text(pdf_path, page_num)

            if page_text:
                # Debug: show first 200 chars of extracted text
                print(f"    Extracted {len(page_text)} characters")

                # Extract amounts from page text
                amounts = extract_amounts_from_text(page_text)
                # Extract N° Socio numbers from page text
                socio_numbers = extract_socio_numbers_from_text(page_text)

                if amounts:
                    print(f"    Found {len(amounts)} amounts, keeping only the last one")
                    # Only keep the last amount found on the page
                    last_amount = amounts[-1]

                    # Get N° Socio number if available
                    socio_info = socio_numbers[0] if socio_numbers else {
                        'socio_number': 'NO_SOCIO_FOUND'
                    }

                    results.append({
                        'sector': sector_name,
                        'page': page_num,
                        'amount_formatted': last_amount['formatted'],
                        'amount_numeric': last_amount['numeric'],
                        'socio_number': socio_info['socio_number']
                    })
                else:
                    # If no amounts found, still record the page
                    print(f"    No amounts found on this page")

                    # Get N° Socio number if available
                    socio_info = socio_numbers[0] if socio_numbers else {
                        'socio_number': 'NO_SOCIO_FOUND'
                    }

                    results.append({
                        'sector': sector_name,
                        'page': page_num,
                        'amount_formatted': 'NO_AMOUNT_FOUND',
                        'amount_numeric': 'NO_AMOUNT_FOUND',
                        'socio_number': socio_info['socio_number']
                    })
            else:
                results.append({
                    'sector': sector_name,
                    'page': page_num,
                    'amount_formatted': 'ERROR_EXTRACTING',
                    'amount_numeric': 'ERROR_EXTRACTING',
                    'socio_number': 'ERROR_EXTRACTING'
                })

    except Exception as e:
        print(f"Error processing {pdf_path}: {e}")

    return results


def extract_socio_number_from_text(text):
    """Extract N° Socio number from page text"""
    try:
        socio_pattern = r'N° Socio:\s*(\d{1,4})'
        matches = re.findall(socio_pattern, text)
        return matches[0] if matches else None
    except Exception as e:
        print(f"Error extracting socio number: {e}")
        return None


def split_pdf_by_socio(pdf_path, output_folder):
    """Split PDF into individual files named by N° Socio"""
    try:
        # Read the original PDF
        reader = PdfReader(pdf_path)
        total_pages = len(reader.pages)

        print(f"Splitting {pdf_path.name}: {total_pages} pages")

        # Process each page
        for page_num, page in enumerate(reader.pages, 1):
            try:
                # Extract text to get N° Socio
                text = page.extract_text()
                socio_number = extract_socio_number_from_text(text)

                # Generate filename
                if socio_number:
                    filename = f"ncon_{socio_number}.pdf"
                else:
                    filename = f"ncon_unknown_page_{page_num:04d}.pdf"

                # Create individual PDF
                writer = PdfWriter()
                writer.add_page(page)

                # Save individual file
                output_path = output_folder / filename
                with open(output_path, "wb") as f:
                    writer.write(f)

                print(f"  Split page {page_num}: {filename}")

            except Exception as page_error:
                print(f"  Error splitting page {page_num}: {page_error}")
                continue

    except Exception as e:
        print(f"Error reading {pdf_path}: {e}")
        return False

    return True


def main():
    # Paths relative to project root (where this script lives)
    workspace_path = Path(__file__).resolve().parent
    boletas_path = workspace_path / "boletas_input"
    output_folder = workspace_path / "app" / "static" / "pdfs"
    output_file = workspace_path / "app" / "static" / "montos" / "payments.json"

    # --- Clean output folders at start ---
    print("Cleaning output folders...")
    clear_directory(output_folder)
    if output_file.exists():
        output_file.unlink()
    output_folder.mkdir(parents=True, exist_ok=True)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Discover all PDFs in boletas_input (use file stem as sector name)
    pdf_files = [(f.stem, f) for f in sorted(boletas_path.glob("*.pdf"))]

    if not pdf_files:
        print(f"No PDF files found in {boletas_path}. Nothing to process.")
        return

    all_results = []

    print("Starting PDF processing...")
    print(f"Looking for PDFs in: {boletas_path}")
    print(f"Output folder: {output_folder}\n")

    # Process each PDF file
    for sector_name, pdf_path in pdf_files:
        print(f"\n=== Processing {sector_name} ===")

        # First split the PDF
        print(f"\n--- Splitting {sector_name} PDF ---")
        split_success = split_pdf_by_socio(pdf_path, output_folder)

        if split_success:
            # Then process for data extraction
            results = process_pdf(pdf_path, sector_name, output_folder)
            all_results.extend(results)
            print(f"Completed {sector_name}: {len(results)} pages processed")
        else:
            print(f"Failed to split {sector_name}")

    if not all_results:
        print("\nNo results to write. Check that PDF files exist and contain text.")
        # Still clear boletas_input so next run can upload new files (keep .gitkeep)
        clear_directory(boletas_path, keep_gitkeep=True)
        return

    # Prepare JSON data structure
    json_data = {
        "version": "1.0",
        "metadata": {
            "total_pages_processed": len(all_results),
            "successful_amount_extractions": 0,
            "successful_socio_extractions": 0,
            "total_amount": 0.0,
            "generated_at": str(datetime.datetime.now())
        },
        "payments": {}
    }

    # Process results and populate JSON data
    total_amount = 0.0

    for result in all_results:
        # Calculate statistics
        if result['amount_numeric'] not in ['NO_AMOUNT_FOUND', 'ERROR_EXTRACTING']:
            try:
                amount = float(result['amount_numeric'])
                total_amount += amount
                json_data["metadata"]["successful_amount_extractions"] += 1
            except (ValueError, TypeError):
                pass

        if result['socio_number'] not in ['NO_SOCIO_FOUND', 'ERROR_EXTRACTING']:
            json_data["metadata"]["successful_socio_extractions"] += 1

        # Add to payments dictionary using socio_number as key
        socio_key = result['socio_number']
        if socio_key not in json_data["payments"]:
            json_data["payments"][socio_key] = []

        payment_entry = {
            "sector": result['sector'],
            "page": result['page'],
            "amount_formatted": result['amount_formatted'],
            "amount_numeric": result['amount_numeric'],
            "socio_number": result['socio_number']
        }

        # Add timestamp if it's a successful amount extraction
        if payment_entry['amount_numeric'] not in ['NO_AMOUNT_FOUND', 'ERROR_EXTRACTING']:
            payment_entry["timestamp"] = datetime.datetime.now().isoformat()

        json_data["payments"][socio_key].append(payment_entry)

    # Update metadata with calculated values
    json_data["metadata"]["total_amount"] = round(total_amount, 2)

    # Write results to JSON file
    print(f"\nWriting results to {output_file}")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)

    # --- Clear boletas_input so new files can be uploaded next month (keep .gitkeep) ---
    print("\nClearing boletas_input...")
    clear_directory(boletas_path, keep_gitkeep=True)

    # Print summary
    print(f"\n=== SUMMARY ===")
    print(f"Total pages processed: {json_data['metadata']['total_pages_processed']}")
    print(f"Successful amount extractions: {json_data['metadata']['successful_amount_extractions']}")
    print(f"Successful N° Socio extractions: {json_data['metadata']['successful_socio_extractions']}")
    print(f"Total amount: ${json_data['metadata']['total_amount']:,.2f}")
    print(f"Results saved to: {output_file}")

    if json_data['metadata']['successful_amount_extractions'] > 0:
        # Show some sample results
        print(f"\n=== SAMPLE RESULTS ===")
        sample_count = 0
        for socio, payments in json_data["payments"].items():
            for payment in payments:
                if payment['amount_numeric'] not in ['NO_AMOUNT_FOUND', 'ERROR_EXTRACTING']:
                    print(f"{payment['sector']} Page {payment['page']}: ${payment['amount_formatted']} (Socio: {payment['socio_number']})")
                    sample_count += 1
                    if sample_count >= 5:
                        break
            if sample_count >= 5:
                break

    # Show unique socio numbers
    unique_socios = [s for s in json_data["payments"].keys() if s not in ['NO_SOCIO_FOUND', 'ERROR_EXTRACTING']]
    print(f"\n=== UNIQUE SOCIO NUMBERS ===")
    print(f"Found {len(unique_socios)} unique socio numbers:")
    for socio in sorted(unique_socios):
        print(f"  - {socio}")


if __name__ == "__main__":
    main()
