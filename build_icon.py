"""生成 icon.ico（构建期用，不打包进应用）"""
from PIL import Image, ImageDraw, ImageFont

img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.ellipse((8, 8, 248, 248), fill=(77, 107, 254, 255))
font = ImageFont.truetype("segoeuib.ttf", 150)
d.text((128, 132), "¥", font=font, fill=(255, 255, 255, 255), anchor="mm")
img.save("icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icon.ico generated")
