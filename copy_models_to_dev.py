import modal, shutil, os

app = modal.App("copy-models-to-dev")

prod_vol = modal.Volume.from_name("fingoh-model-vol")
dev_vol  = modal.Volume.from_name("fingoh-model-vol-dev", create_if_missing=True)

@app.function(volumes={"/prod": prod_vol, "/dev": dev_vol})
def copy_models():
    for item in os.listdir("/prod"):
        src = f"/prod/{item}"
        dst = f"/dev/{item}"
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
            print(f"  Copied dir {item}")
        else:
            shutil.copy2(src, dst)
            print(f"  Copied file {item}")
    dev_vol.commit()
    print("Done")

@app.local_entrypoint()
def main():
    copy_models.remote()
