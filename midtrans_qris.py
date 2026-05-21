import json
import os
import requests
import base64
import qrcode
import io
from datetime import datetime
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

# Midtrans Configuration
MIDTRANS_SERVER_KEY = os.getenv("MIDTRANS_SERVER_KEY")
MIDTRANS_CLIENT_KEY = os.getenv("MIDTRANS_CLIENT_KEY")
MIDTRANS_IS_PRODUCTION = os.getenv("MIDTRANS_IS_PRODUCTION", "false").lower() == "true"

# Midtrans URLs
if MIDTRANS_IS_PRODUCTION:
    MIDTRANS_API_URL = "https://api.midtrans.com/v2"
    MIDTRANS_SNAP_URL = "https://app.midtrans.com/snap/v1"
else:
    MIDTRANS_API_URL = "https://api.sandbox.midtrans.com/v2"
    MIDTRANS_SNAP_URL = "https://app.sandbox.midtrans.com/snap/v1"

class MidtransQRIS:
    def __init__(self):
        self.server_key = MIDTRANS_SERVER_KEY
        self.client_key = MIDTRANS_CLIENT_KEY
        self.is_production = MIDTRANS_IS_PRODUCTION
        
        # Create authorization header
        auth_string = f"{self.server_key}:"
        auth_bytes = auth_string.encode('ascii')
        auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
        self.headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Authorization': f'Basic {auth_b64}'
        }
    
    def generate_order_id(self, chat_id):
        """Generate unique order ID"""
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"KOST-{chat_id}-{timestamp}"
    
    def create_qris_payment(self, order_id, amount, customer_name, customer_phone=""):
        """Create QRIS payment transaction"""
        try:
            # Transaction details
            transaction_details = {
                "order_id": order_id,
                "gross_amount": amount
            }
            
            # Customer details
            customer_details = {
                "first_name": customer_name,
                "phone": customer_phone
            }
            
            # Item details
            item_details = [{
                "id": "kost_rent",
                "price": amount,
                "quantity": 1,
                "name": f"Tagihan Kost - {customer_name}"
            }]
            
            # Complete payload
            payload = {
                "payment_type": "qris",
                "transaction_details": transaction_details,
                "customer_details": customer_details,
                "item_details": item_details,
                "qris": {
                    "acquirer": "gopay"
                }
            }
            
            print(f"🔄 Making Midtrans API request for order: {order_id}")
            
            # Make API request
            response = requests.post(
                f"{MIDTRANS_API_URL}/charge",
                headers=self.headers,
                json=payload
            )
            
            print(f"📡 HTTP Status Code: {response.status_code}")
            
            # Treat any successful HTTP response (2xx) as valid
            if 200 <= response.status_code < 300:
                try:
                    result = response.json()
                except ValueError:
                    print("❌ Failed to decode Midtrans JSON response")
                    return {
                        "success": False,
                        "error": "Invalid Midtrans response",
                        "message": response.text
                    }

                print(f"📋 Midtrans Response: {json.dumps(result, indent=2)}")
                
                # Check Midtrans status_code (can be string or int)
                midtrans_status = str(result.get("status_code"))
                print(f"🔍 Midtrans status_code: {midtrans_status} (type: {type(result.get('status_code'))})")
                
                if midtrans_status in {"200", "201"}:
                    # Extract payment URL from actions array
                    actions = result.get("actions", [])
                    payment_url = ""
                    if actions:
                        payment_url = actions[0].get("url", "")
                    
                    print("✅ QRIS payment created successfully!")
                    print(f"   Transaction ID: {result.get('transaction_id')}")
                    print(f"   QR String length: {len(result.get('qr_string', ''))}")
                    
                    return {
                        "success": True,
                        "order_id": order_id,
                        "transaction_id": result.get("transaction_id"),
                        "qr_string": result.get("qr_string"),
                        "deeplink_url": payment_url,
                        "status": result.get("transaction_status"),
                        "payment_url": payment_url,
                        "raw_response": result
                    }
                else:
                    print(f"❌ Midtrans status_code mismatch: expected '200/201', got '{midtrans_status}'")
                    return {
                        "success": False,
                        "error": f"Midtrans Error: {midtrans_status}",
                        "message": result.get("status_message", "Unknown Midtrans error"),
                        "raw_response": result
                    }
            else:
                print(f"❌ HTTP Error: {response.status_code}")
                return {
                    "success": False,
                    "error": f"HTTP Error: {response.status_code}",
                    "message": response.text
                }
                
        except requests.exceptions.RequestException as e:
            print(f"💥 Request Exception in create_qris_payment: {str(e)}")
            return {
                "success": False,
                "error": "Request Exception occurred",
                "message": str(e)
            }
        except json.JSONDecodeError as e:
            print(f"💥 JSON Decode Exception in create_qris_payment: {str(e)}")
            return {
                "success": False,
                "error": "JSON Decode Exception occurred",
                "message": str(e)
            }
        except Exception as e:
            print(f"💥 General Exception in create_qris_payment: {str(e)}")
            print(f"💥 Exception type: {type(e)}")
            import traceback
            print(f"💥 Traceback: {traceback.format_exc()}")
            return {
                "success": False,
                "error": "Exception occurred",
                "message": str(e)
            }
    
    def check_payment_status(self, order_id):
        """Check payment status"""
        try:
            response = requests.get(
                f"{MIDTRANS_API_URL}/{order_id}/status",
                headers=self.headers
            )
            
            if response.status_code == 200:
                result = response.json()
                return {
                    "success": True,
                    "order_id": order_id,
                    "status": result.get("transaction_status"),
                    "payment_type": result.get("payment_type"),
                    "transaction_time": result.get("transaction_time"),
                    "settlement_time": result.get("settlement_time", ""),
                    "gross_amount": result.get("gross_amount")
                }
            else:
                return {
                    "success": False,
                    "error": f"Status check failed: {response.status_code}",
                    "message": response.text
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": "Exception occurred",
                "message": str(e)
            }
    
    def cancel_payment(self, order_id):
        try:
            response = requests.post(
                f"{MIDTRANS_API_URL}/{order_id}/cancel",
                headers=self.headers,
                timeout=30
            )

            result = response.json()

            if response.status_code in [200, 201]:
                return {
                    "success": True,
                    "data": result,
                    "message": result.get("status_message", "Transaction cancelled")
                }

            return {
                "success": False,
                "data": result,
                "message": result.get("status_message", "Failed to cancel transaction")
            }

        except Exception as e:
            return {
                "success": False,
                "data": None,
                "message": str(e)
            }

    def generate_qr_image(self, qr_string, customer_name, amount, order_id):
        """Generate QR code image with billing information"""
        try:
            # Create QR code
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(qr_string)
            qr.make(fit=True)

            # Create QR code image
            qr_img = qr.make_image(fill_color="black", back_color="white")
            
            # Create a larger canvas for the complete image
            canvas_width = 400
            canvas_height = 600
            canvas = Image.new('RGB', (canvas_width, canvas_height), 'white')
            
            # Resize QR code to fit nicely
            qr_size = 300
            qr_img = qr_img.resize((qr_size, qr_size))
            
            # Position QR code in center
            qr_x = (canvas_width - qr_size) // 2
            qr_y = 80
            canvas.paste(qr_img, (qr_x, qr_y))
            
            # Add text information
            draw = ImageDraw.Draw(canvas)
            
            try:
                # Try to use a better font
                title_font = ImageFont.truetype("arial.ttf", 24)
                info_font = ImageFont.truetype("arial.ttf", 16)
                small_font = ImageFont.truetype("arial.ttf", 12)
            except:
                # Fallback to default font
                title_font = ImageFont.load_default()
                info_font = ImageFont.load_default()
                small_font = ImageFont.load_default()
            
            # Title
            title_text = "TAGIHAN KOST - QRIS"
            title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
            title_width = title_bbox[2] - title_bbox[0]
            title_x = (canvas_width - title_width) // 2
            draw.text((title_x, 20), title_text, fill="black", font=title_font)
            
            # Customer info
            y_offset = qr_y + qr_size + 20
            
            # Customer name
            name_text = f"Nama: {customer_name}"
            name_bbox = draw.textbbox((0, 0), name_text, font=info_font)
            name_width = name_bbox[2] - name_bbox[0]
            name_x = (canvas_width - name_width) // 2
            draw.text((name_x, y_offset), name_text, fill="black", font=info_font)
            
            # Amount
            amount_text = f"Jumlah: {format_currency(amount)}"
            amount_bbox = draw.textbbox((0, 0), amount_text, font=info_font)
            amount_width = amount_bbox[2] - amount_bbox[0]
            amount_x = (canvas_width - amount_width) // 2
            draw.text((amount_x, y_offset + 30), amount_text, fill="black", font=info_font)
            
            # Order ID
            order_text = f"Order ID: {order_id}"
            order_bbox = draw.textbbox((0, 0), order_text, font=small_font)
            order_width = order_bbox[2] - order_bbox[0]
            order_x = (canvas_width - order_width) // 2
            draw.text((order_x, y_offset + 60), order_text, fill="gray", font=small_font)
            
            # Instructions
            instruction_text = "Scan QR Code untuk pembayaran"
            instruction_bbox = draw.textbbox((0, 0), instruction_text, font=small_font)
            instruction_width = instruction_bbox[2] - instruction_bbox[0]
            instruction_x = (canvas_width - instruction_width) // 2
            draw.text((instruction_x, y_offset + 90), instruction_text, fill="gray", font=small_font)
            
            # Convert to bytes
            img_byte_arr = io.BytesIO()
            canvas.save(img_byte_arr, format='PNG')
            img_byte_arr.seek(0)
            
            return {
                "success": True,
                "image_bytes": img_byte_arr,
                "filename": f"qris_{order_id}.png"
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": "QR image generation failed",
                "message": str(e)
            }

# Utility functions
def format_currency(amount):
    """Format currency to Indonesian Rupiah"""
    return f"Rp{amount:,}".replace(",", ".")

# def get_payment_status_emoji(status):
#     """Get emoji for payment status"""
#     status_emojis = {
#         "pending": "⏳",
#         "settlement": "✅",
#         "capture": "✅",
#         "deny": "❌",
#         "cancel": "❌",
#         "expire": "⏰",
#         "failure": "❌"
#     }
#     return status_emojis.get(status, "❓")

# def get_payment_status_text(status):
#     """Get readable text for payment status"""
#     status_texts = {
#         "pending": "Menunggu Pembayaran",
#         "settlement": "Pembayaran Berhasil",
#         "capture": "Pembayaran Berhasil",
#         "deny": "Pembayaran Ditolak",
#         "cancel": "Pembayaran Dibatalkan",
#         "expire": "Pembayaran Kedaluwarsa",
#         "failure": "Pembayaran Gagal"
#     }
#     return status_texts.get(status, "Status Tidak Diketahui")