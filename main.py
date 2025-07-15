import tkinter as tk
from tkinter import scrolledtext
import threading
import time
import requests
from woocommerce import API
import base64
import re
import json

# WooCommerce API Config 
wcapi = API(
    url="***",
    consumer_key="***",
    consumer_secret="***",
    version="***",
    timeout=30
)

stop_requested = False
log_counter = 1

auto_run_flag = False
current_batch_index = 0
product_batches = []

# Thread-safe log helper
thread_safe_log = None  # Will be set after log_widget is created

def set_thread_safe_log(log_widget):
    import threading
    def _log(msg):
        if threading.current_thread() is threading.main_thread():
            log_widget.insert(tk.END, msg)
            log_widget.see(tk.END)
        else:
            log_widget.after(0, lambda: log_widget.insert(tk.END, msg))
            log_widget.after(0, lambda: log_widget.see(tk.END))
    global thread_safe_log
    thread_safe_log = _log

def check_image_url_exists(url):
    try:
        response = requests.head(url, timeout=5)
        return response.status_code == 200
    except requests.RequestException:
        return False

def get_image_id_by_url(url):
    filename = url.split("/")[-1]
    api_url = "***"
    auth = (wcapi.consumer_key, wcapi.consumer_secret)
    headers = {}
    userpass = f"{wcapi.consumer_key}:{wcapi.consumer_secret}"
    headers["Authorization"] = "Basic " + base64.b64encode(userpass.encode()).decode()
    per_page = 100
    max_pages = 10
    for page in range(1, max_pages + 1):
        response = requests.get(api_url, params={"search": filename, "per_page": per_page, "page": page}, headers=headers)
        if response.status_code == 200:
            items = json.loads(response.content.decode('utf-8-sig'))
            for item in items:
                if item.get("source_url") == url:
                    image_id = item.get("id")
                    print(f"[LOG] Found image ID {image_id} for URL: {url}")
                    return image_id
            if len(items) < per_page:
                break  # No more pages
        else:
            break  # API error, stop
    print(f"[LOG] No image ID found for URL: {url}")
    return None

def get_image_id_by_title(title, wcapi=None):
    import requests
    import re
    WC_URL = wcapi.url if wcapi else "***"
    api_url = WC_URL.rstrip('/') + "/wp-json/wp/v2/media"
    per_page = 100
    max_pages = 10
    title_norm = re.sub(r'\W+', '', title).strip().lower()
    found = False
    for page in range(1, max_pages + 1):
        response = requests.get(api_url, params={"search": title, "per_page": per_page, "page": page})
        if response.status_code == 200:
            items = json.loads(response.content.decode('utf-8-sig'))
            for item in items:
                t = item.get("title", {}).get("rendered", "")
                slug = item.get("slug", "")
                source_url = item.get("source_url", "")
                filename = source_url.split("/")[-1].split(".")[0] if source_url else ""
                t_norm = re.sub(r'\W+', '', t).strip().lower()
                slug_norm = re.sub(r'\W+', '', slug).strip().lower()
                filename_norm = re.sub(r'\W+', '', filename).strip().lower()
                print(f"[DEBUG] Found: title='{t}', slug='{slug}', filename='{filename}' (ID: {item.get('id')})")
                if t_norm == title_norm or slug_norm == title_norm or filename_norm == title_norm:
                    image_id = item.get("id")
                    print(f"[LOG] Found image ID {image_id} for title: {title}")
                    return image_id
        else:
            print(f"[ERROR] API error: {response.status_code}")
            break
        if found or len(items) < per_page:
            break
    print(f"[LOG] No image ID found for title: {title}")
    return None

def get_all_product_ids():
    product_ids = []
    page = 1
    while True:
        response_raw = wcapi.get("products", params={"per_page": 100, "page": page})
        try:
            response = json.loads(response_raw.content.decode('utf-8-sig'))
        except Exception as e:
            print("JSON ERROR:", e)
            print("Returned content:", response_raw.text[:500])
            raise
        if not response:
            break
        product_ids.extend([product['id'] for product in response])
        page += 1
    return product_ids

def get_all_product_ids_with_order(wcapi, order="oldest"):
    product_ids = []
    page = 1
    while True:
        response_raw = wcapi.get("products", params={"per_page": 100, "page": page, "orderby": "date", "order": "asc" if order=="oldest" else "desc"})
        try:
            response = json.loads(response_raw.content.decode('utf-8-sig'))
        except Exception as e:
            print("JSON ERROR:", e)
            print("Returned content:", response_raw.text[:500])
            raise
        if not response:
            break
        product_ids.extend([product['id'] for product in response])
        page += 1
    return product_ids

def batch_product_ids(product_ids, batch_size):
    for i in range(0, len(product_ids), batch_size):
        yield product_ids[i:i+batch_size]

def update_product_gallery(product_id, new_image_urls, replace_limit, log_widget):
    global log_counter
    try:
        product_raw = wcapi.get(f"products/{product_id}")
        product = json.loads(product_raw.content.decode('utf-8-sig'))
        product_title = product.get("name", f"ID {product_id}")
        current_images = product.get("images", [])

        # ALWAYS KEEP OLD IMAGES - only add new images
        final_images = current_images.copy()

        # Add new images, check for duplicates
        for url in new_image_urls:
            if not check_image_url_exists(url):
                thread_safe_log(f"{log_counter}. Image URL not found or unreachable: {url}\n")
                log_counter += 1
                continue
            # Check if URL already exists in gallery
            url_exists = any(img.get("src") == url or (img.get("id") and img.get("src") is None) for img in final_images)
            if url_exists:
                thread_safe_log(f"{log_counter}. Image URL already exists in gallery: {url}\n")
                log_counter += 1
                continue
            # Check if image already exists in Media Library
            image_id = get_image_id_by_url(url)
            if image_id:
                final_images.append({"id": image_id})
                thread_safe_log(f"{log_counter}. Image added by ID: {url} (ID: {image_id})\n")
            else:
                thread_safe_log(f"{log_counter}. Image not found in Media Library: {url}\n")
            log_counter += 1

        data = {"images": final_images}
        response = wcapi.put(f"products/{product_id}", data)

        if response.status_code == 200:
            thread_safe_log(f"{log_counter}. Updated {product_title} - Total images: {len(final_images)}\n")
        else:
            thread_safe_log(f"{log_counter}. Failed {product_title} (Status {response.status_code})\n")

        log_counter += 1

    except Exception as e:
        thread_safe_log(f"{log_counter}. Error updating {product_id}: {e}\n")
        log_counter += 1

def update_product_gallery_by_id(product_id, image_id, mode, position, position_index, wcapi, log_widget):
    try:
        # Get product info
        product_raw = wcapi.get(f"products/{product_id}")
        product = json.loads(product_raw.content.decode('utf-8-sig'))
        gallery = product.get("images", [])
        # Get current IDs list
        gallery_ids = [img.get("id") for img in gallery if img.get("id")]
        # Remove mode
        if mode == "remove":
            if image_id in gallery_ids:
                gallery_ids = [i for i in gallery_ids if i != image_id]
                thread_safe_log(f"Product {product_id}: Removed image ID {image_id} from gallery.\n")
            else:
                thread_safe_log(f"Product {product_id}: Image ID {image_id} not in gallery.\n")
        # Add mode
        elif mode == "add":
            if image_id in gallery_ids:
                thread_safe_log(f"Product {product_id}: Image ID {image_id} already in gallery.\n")
            else:
                if position == "start":
                    gallery_ids = [image_id] + gallery_ids
                elif position == "end":
                    gallery_ids = gallery_ids + [image_id]
                elif position == "index":
                    try:
                        idx = int(position_index)
                        if idx < 0: idx = 0
                        if idx > len(gallery_ids): idx = len(gallery_ids)
                        gallery_ids = gallery_ids[:idx] + [image_id] + gallery_ids[idx:]
                    except Exception:
                        gallery_ids = gallery_ids + [image_id]
                thread_safe_log(f"Product {product_id}: Added image ID {image_id} to gallery at {position}.\n")
        # Update gallery
        new_gallery = [{"id": i} for i in gallery_ids]
        data = {"images": new_gallery}
        response = wcapi.put(f"products/{product_id}", data)
        if response.status_code == 200:
            thread_safe_log(f"Product {product_id}: Gallery updated. Total images: {len(new_gallery)}\n")
        else:
            thread_safe_log(f"Product {product_id}: Update failed (Status {response.status_code})\n")
        log_widget.see(tk.END)
    except Exception as e:
        thread_safe_log(f"Product {product_id}: Error: {e}\n")
        log_widget.see(tk.END)

def batch_update(new_image_urls, replace_limit, log_widget, confirm_button, stop_button):
    global stop_requested
    stop_requested = False

    thread_safe_log("Fetching all product IDs...\n")
    product_ids = get_all_product_ids()
    thread_safe_log(f"Found {len(product_ids)} products.\n")

    # Handle the number of products to update
    try:
        if replace_limit.upper() == "ALL":
            products_to_update = product_ids
            thread_safe_log(f"Will update ALL {len(product_ids)} products.\n")
        else:
            limit = int(replace_limit)
            if limit <= 0:
                thread_safe_log("Invalid number. Please enter a positive number or 'ALL'.\n")
                confirm_button.config(state=tk.NORMAL)
                stop_button.config(state=tk.DISABLED)
                return
            products_to_update = product_ids[:limit]
            thread_safe_log(f"Will update first {limit} products.\n")
    except ValueError:
        thread_safe_log("Invalid number. Please enter a positive number or 'ALL'.\n")
        confirm_button.config(state=tk.NORMAL)
        stop_button.config(state=tk.DISABLED)
        return

    for product_id in products_to_update:
        if stop_requested:
            thread_safe_log("Process stopped by user.\n")
            log_widget.see(tk.END)
            break
        update_product_gallery(product_id, new_image_urls, replace_limit, log_widget)
        time.sleep(1)

    confirm_button.config(state=tk.NORMAL)
    stop_button.config(state=tk.DISABLED)

def start_process(image_urls_entry, replace_limit_entry, log_widget, confirm_button, stop_button):
    confirm_button.config(state=tk.DISABLED)
    stop_button.config(state=tk.NORMAL)

    new_image_urls = [url.strip() for url in image_urls_entry.get().split(",") if url.strip()]
    replace_limit = replace_limit_entry.get().strip() or "ALL"

    threading.Thread(target=batch_update, args=(new_image_urls, replace_limit, log_widget, confirm_button, stop_button)).start()

def stop_process(confirm_button, stop_button):
    global stop_requested
    stop_requested = True
    confirm_button.config(state=tk.NORMAL)
    stop_button.config(state=tk.DISABLED)

def run_once(batch_size_entry, image_title_entry, mode_var, position_var, position_index_entry, order_var, log_widget):
    try:
        batch_size = int(batch_size_entry.get())
        image_title = image_title_entry.get().strip()
        mode = mode_var.get()
        position = position_var.get()
        position_index = position_index_entry.get().strip()
        order = order_var.get()
        thread_safe_log(f"--- Run Once: Batch size {batch_size}, Image '{image_title}', Mode {mode}, Position {position}, Order {order} ---\n")
        log_widget.see(tk.END)
        # Get product list
        product_ids = get_all_product_ids_with_order(wcapi, order)
        if not product_ids:
            thread_safe_log("No products found!\n")
            log_widget.see(tk.END)
            return
        # Split into batches
        batches = list(batch_product_ids(product_ids, batch_size))
        if not batches:
            thread_safe_log("No batch to process!\n")
            log_widget.see(tk.END)
            return
        # Find image_id only once
        image_id = get_image_id_by_title(image_title, wcapi)
        if not image_id:
            thread_safe_log(f"Image with title '{image_title}' not found. Batch skipped.\n")
            log_widget.see(tk.END)
            return
        thread_safe_log(f"[LOG] Found image ID {image_id} for title: {image_title}\n")
        log_widget.see(tk.END)
        # Process the first batch
        for product_id in batches[0]:
            update_product_gallery_by_id(product_id, image_id, mode, position if position != "index" else "index", position_index, wcapi, log_widget)
        thread_safe_log(f"--- Batch done ({len(batches[0])} products) ---\n")
        log_widget.see(tk.END)
    except Exception as e:
        thread_safe_log(f"Error: {e}\n")
        log_widget.see(tk.END)

def auto_run_batches(batch_size_entry, image_title_entry, mode_var, position_var, position_index_entry, order_var, log_widget, auto_run_btn, stop_btn):
    print("[DEBUG] auto_run_batches called")
    global auto_run_flag, current_batch_index, product_batches
    auto_run_flag = True
    auto_run_btn.config(state=tk.DISABLED)
    stop_btn.config(state=tk.NORMAL)
    try:
        batch_size = int(batch_size_entry.get())
        image_title = image_title_entry.get().strip()
        mode = mode_var.get()
        position = position_var.get()
        position_index = position_index_entry.get().strip()
        order = order_var.get()
        print(f"[DEBUG] batch_size={batch_size}, image_title={image_title}, mode={mode}, position={position}, order={order}")
        if not product_batches:
            product_ids = get_all_product_ids_with_order(wcapi, order)
            print(f"[DEBUG] Number of products: {len(product_ids)}")
            product_batches = list(batch_product_ids(product_ids, batch_size))
            print(f"[DEBUG] Number of batches: {len(product_batches)}")
            current_batch_index = 0
        # Find image_id only once for the entire Auto Run
        image_id = get_image_id_by_title(image_title, wcapi)
        print(f"[DEBUG] image_id found: {image_id}")
        if not image_id:
            thread_safe_log(f"Image with title '{image_title}' not found. Auto Run skipped.\n")
            log_widget.see(tk.END)
            auto_run_btn.config(state=tk.NORMAL)
            stop_btn.config(state=tk.DISABLED)
            return
        thread_safe_log(f"[LOG] Found image ID {image_id} for title: {image_title}\n")
        log_widget.see(tk.END)
        def run_next_batch():
            print("[DEBUG] run_next_batch called")
            global auto_run_flag, current_batch_index, product_batches
            print(f"[DEBUG] AutoRun: current_batch_index={current_batch_index}, total_batches={len(product_batches)}")
            if not auto_run_flag or current_batch_index >= len(product_batches):
                thread_safe_log("--- Auto Run finished ---\n")
                log_widget.see(tk.END)
                auto_run_btn.config(state=tk.NORMAL)
                stop_btn.config(state=tk.DISABLED)
                return
            thread_safe_log(f"--- Auto Run: Batch {current_batch_index+1}/{len(product_batches)} ---\n")
            log_widget.see(tk.END)
            try:
                for product_id in product_batches[current_batch_index]:
                    update_product_gallery_by_id(product_id, image_id, mode, position if position != "index" else "index", position_index, wcapi, log_widget)
                thread_safe_log(f"--- Batch {current_batch_index+1} done ---\n")
                log_widget.see(tk.END)
            except Exception as e:
                print(f"[ERROR] Exception in batch: {e}")
            current_batch_index += 1
            if auto_run_flag:
                root = log_widget.master
                root.after(5000, run_next_batch)
        try:
            run_next_batch()
        except Exception as e:
            print(f"[ERROR] Exception when calling run_next_batch: {e}")
    except Exception as e:
        print(f"[ERROR] Global exception auto_run_batches: {e}")
        thread_safe_log(f"Error: {e}\n")
        log_widget.see(tk.END)
        auto_run_btn.config(state=tk.NORMAL)
        stop_btn.config(state=tk.DISABLED)

def stop_auto_run(auto_run_btn, stop_btn, log_widget):
    global auto_run_flag
    auto_run_flag = False
    auto_run_btn.config(state=tk.NORMAL)
    stop_btn.config(state=tk.DISABLED)
    thread_safe_log("--- Auto Run stopped by user ---\n")
    log_widget.see(tk.END)

def reset_progress(log_widget):
    global current_batch_index, product_batches, auto_run_flag
    current_batch_index = 0
    product_batches = []
    auto_run_flag = False
    thread_safe_log("--- Progress reset ---\n")
    log_widget.see(tk.END)

# --- Main GUI ---
def create_gui():
    root = tk.Tk()
    root.title("WooCommerce Batch Image Inserter")

    # Batch size
    tk.Label(root, text="Batch Size:").grid(row=0, column=0, sticky="e", padx=5, pady=5)
    batch_size_entry = tk.Entry(root, width=10)
    batch_size_entry.insert(0, "10")
    batch_size_entry.grid(row=0, column=1, sticky="w", padx=5, pady=5)

    # Image Title
    tk.Label(root, text="Image Title:").grid(row=1, column=0, sticky="e", padx=5, pady=5)
    image_title_entry = tk.Entry(root, width=40)
    image_title_entry.grid(row=1, column=1, sticky="w", padx=5, pady=5)

    # Mode (Add/Remove)
    tk.Label(root, text="Mode:").grid(row=2, column=0, sticky="e", padx=5, pady=5)
    mode_var = tk.StringVar(value="add")
    tk.Radiobutton(root, text="Add", variable=mode_var, value="add").grid(row=2, column=1, sticky="w")
    tk.Radiobutton(root, text="Remove", variable=mode_var, value="remove").grid(row=2, column=1, sticky="e")

    # Insert Position
    tk.Label(root, text="Insert Position:").grid(row=3, column=0, sticky="e", padx=5, pady=5)
    position_var = tk.StringVar(value="end")
    tk.Radiobutton(root, text="Start", variable=position_var, value="start").grid(row=3, column=1, sticky="w")
    tk.Radiobutton(root, text="End", variable=position_var, value="end").grid(row=3, column=1)
    tk.Label(root, text="or Index:").grid(row=3, column=2, sticky="e")
    position_index_entry = tk.Entry(root, width=5)
    position_index_entry.grid(row=3, column=3, sticky="w")

    # Product Order
    tk.Label(root, text="Product Order:").grid(row=4, column=0, sticky="e", padx=5, pady=5)
    order_var = tk.StringVar(value="oldest")
    tk.Radiobutton(root, text="Oldest to Newest", variable=order_var, value="oldest").grid(row=4, column=1, sticky="w")
    tk.Radiobutton(root, text="Newest to Oldest", variable=order_var, value="newest").grid(row=4, column=1)

    # Buttons
    run_once_btn = tk.Button(root, text="Run Once", command=lambda: run_once(batch_size_entry, image_title_entry, mode_var, position_var, position_index_entry, order_var, log_widget))
    run_once_btn.grid(row=5, column=0, padx=5, pady=10)
    auto_run_btn = tk.Button(root, text="Start Auto Run", command=lambda: threading.Thread(target=auto_run_batches, args=(batch_size_entry, image_title_entry, mode_var, position_var, position_index_entry, order_var, log_widget, auto_run_btn, stop_btn)).start())
    auto_run_btn.grid(row=5, column=1, padx=5, pady=10)
    stop_btn = tk.Button(root, text="Stop Auto Run", state=tk.DISABLED, command=lambda: stop_auto_run(auto_run_btn, stop_btn, log_widget))
    stop_btn.grid(row=5, column=2, padx=5, pady=10)
    reset_btn = tk.Button(root, text="Reset Progress", command=lambda: reset_progress(log_widget))
    reset_btn.grid(row=5, column=3, padx=5, pady=10)

    # Log
    tk.Label(root, text="Log:").grid(row=6, column=0, sticky="nw", padx=5)
    log_widget = scrolledtext.ScrolledText(root, width=80, height=15)
    log_widget.grid(row=6, column=1, columnspan=3, padx=5, pady=5)
    set_thread_safe_log(log_widget)

    root.mainloop()

if __name__ == "__main__":
    create_gui()
